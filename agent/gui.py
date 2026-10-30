"""Tkinter desktop UI for the ACE Developer Agent."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, scrolledtext, ttk

from . import config as cfgmod
from . import doctor, jira, pipeline, terminal, vcs
from .settings_dialog import SettingsDialog
from .theme import T, apply_theme, style_ttk

# icon per step state, plus the palette ROLE that colours it. The role is
# resolved at draw time so the colour follows the active theme.
STATE_ICON = {
    pipeline.PENDING: ("( )", "MUTED"),
    pipeline.RUNNING: (">>>", "ACCENT"),
    pipeline.DONE: ("[x]", "OK"),
    pipeline.FAILED: ("[!]", "BAD"),
    pipeline.SKIPPED: ("[-]", "WARN"),
}

GH_LOGIN_CMD = "gh auth login --hostname github.com --git-protocol https --web"


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("ACE DEVELOPER AGENT")
        self.geometry("1180x840")
        self.minsize(960, 680)

        self.cfg = cfgmod.load()
        T.set(self.cfg.theme if self.cfg.theme in ("dark", "light") else "dark")
        self.configure(bg=T.BG)

        self.ui_queue: queue.Queue = queue.Queue()
        self.approval_queue: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.pipeline: pipeline.Pipeline | None = None
        self.step_labels: list[tk.Label] = []
        self._step_states: dict[int, str] = {}
        self.pr_url = ""

        self._build_style()
        self._build_ui()
        self.after(60, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # make sure the CLIs we drive are installed before anything else
        self.after(200, self._check_tools)
        # check GitHub auth once, in the background, so startup is not blocked
        self.after(700, self._check_github_auth)

    def _check_tools(self) -> None:
        """Tell the user plainly if git / gh / claude are not installed."""
        missing = doctor.missing_required()
        if not missing:
            return
        names = ", ".join(s.name for s in missing)
        hints = "\n\n".join(
            "{}:\n  {}".format(s.name, s.install_hint) for s in missing)
        self.status_var.set("Missing required tools: " + names)
        messagebox.showerror(
            "ACE DEVELOPER AGENT - setup needed",
            "This tool drives command-line programs that are not installed "
            "on this machine:\n\n" + hints
            + "\n\nInstall them, then restart the app.",
        )

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------

    def _build_style(self) -> None:
        style_ttk(self)

    def _build_ui(self) -> None:
        # ---- top bar: issue key + actions ----
        top = tk.Frame(self, bg=T.BG)
        top.pack(fill="x", padx=14, pady=(12, 6))

        tk.Label(top, text="Jira issue", bg=T.BG, fg=T.MUTED,
                 font=("Segoe UI", 10)).pack(side="left")

        self.issue_var = tk.StringVar()
        self.issue_entry = tk.Entry(
            top, textvariable=self.issue_var, font=("Consolas", 15),
            bg=T.ENTRY_BG, fg=T.FG, insertbackground=T.FG, relief="flat",
            width=18,
        )
        self.issue_entry.pack(side="left", padx=(10, 10), ipady=6, ipadx=6)
        self.issue_entry.insert(0, "ACE-")
        self.issue_entry.icursor("end")
        self.issue_entry.bind("<Return>", lambda e: self.start())
        self.issue_entry.focus_set()

        self.run_btn = tk.Button(
            top, text="Run", command=self.start, bg=T.ACCENT, fg=T.BTN_FG,
            font=("Segoe UI", 10, "bold"), relief="flat", padx=22, pady=6,
            activeforeground=T.BTN_FG, cursor="hand2",
        )
        self.run_btn.pack(side="left")

        self.cancel_btn = tk.Button(
            top, text="Cancel", command=self.cancel, bg=T.PANEL, fg=T.MUTED,
            font=("Segoe UI", 10), relief="flat", padx=16, pady=6,
            state="disabled", cursor="hand2",
        )
        self.cancel_btn.pack(side="left", padx=(8, 0))

        tk.Button(
            top, text="Settings", command=self.open_settings, bg=T.PANEL,
            fg=T.FG, font=("Segoe UI", 10), relief="flat", padx=16, pady=6,
            cursor="hand2",
        ).pack(side="right")

        self.theme_btn = tk.Button(
            top, command=self.toggle_theme, bg=T.PANEL, fg=T.FG,
            font=("Segoe UI", 10), relief="flat", padx=14, pady=6,
            cursor="hand2",
        )
        self.theme_btn.pack(side="right", padx=(0, 8))
        self._sync_theme_button()

        # ---- issue summary line ----
        self.summary_var = tk.StringVar(value="Enter a Jira key and press Run.")
        tk.Label(self, textvariable=self.summary_var, bg=T.BG, fg=T.MUTED,
                 font=("Segoe UI", 10), anchor="w", wraplength=1100,
                 justify="left").pack(fill="x", padx=16)

        self.progress = ttk.Progressbar(self, mode="determinate",
                                        maximum=len(pipeline.STEPS))
        self.progress.pack(fill="x", padx=14, pady=(8, 4))

        # ---- body: steps | tabs ----
        body = tk.Frame(self, bg=T.BG)
        body.pack(fill="both", expand=True, padx=14, pady=6)

        steps = tk.Frame(body, bg=T.PANEL)
        steps.pack(side="left", fill="y", padx=(0, 12))
        tk.Label(steps, text="PIPELINE", bg=T.PANEL, fg=T.MUTED,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14,
                                                    pady=(12, 6))
        for i, name in enumerate(pipeline.STEPS):
            lbl = tk.Label(
                steps, text="( )  {}. {}".format(i + 1, name), bg=T.PANEL,
                fg=T.MUTED, font=("Consolas", 10), anchor="w", justify="left",
            )
            lbl.pack(anchor="w", padx=14, pady=3)
            self.step_labels.append(lbl)

        tk.Frame(steps, bg=T.PANEL, height=10).pack()
        tk.Label(steps, text="EXTRA INSTRUCTIONS (optional)", bg=T.PANEL,
                 fg=T.MUTED, font=("Segoe UI", 9, "bold")).pack(
            anchor="w", padx=14, pady=(10, 4))
        self.extra_text = tk.Text(steps, height=7, width=34, bg=T.ENTRY_BG,
                                  fg=T.FG, insertbackground=T.FG, relief="flat",
                                  font=("Segoe UI", 9), wrap="word")
        self.extra_text.pack(padx=14, pady=(0, 14))

        nb = ttk.Notebook(body)
        nb.pack(side="left", fill="both", expand=True)
        self.notebook = nb

        self.log_text = self._make_text_tab(nb, "Activity")
        self.issue_text = self._make_text_tab(nb, "Jira")
        self.diff_text = self._make_text_tab(nb, "Diff")
        self.diff_text.tag_configure("add", foreground=T.DIFF_ADD)
        self.diff_text.tag_configure("del", foreground=T.DIFF_DEL)
        self.diff_text.tag_configure("head", foreground=T.DIFF_HEAD,
                                     font=("Consolas", 10, "bold"))

        self._build_terminal_tab(nb)

        # ---- approval bar ----
        self.approval_bar = tk.Frame(self, bg=T.PANEL)
        self.approval_msg = tk.Label(
            self.approval_bar, text="", bg=T.PANEL, fg=T.WARN,
            font=("Segoe UI", 10, "bold"),
        )
        self.approval_msg.pack(side="left", padx=14, pady=10)
        tk.Button(self.approval_bar, text="Approve & Push", command=lambda:
                  self._answer("approve"), bg=T.OK, fg=T.BTN_FG, relief="flat",
                  font=("Segoe UI", 10, "bold"), padx=18, pady=6,
                  cursor="hand2").pack(side="right", padx=(6, 14), pady=8)
        tk.Button(self.approval_bar, text="Regenerate", command=lambda:
                  self._answer("regenerate"), bg=T.WARN, fg=T.BTN_FG,
                  relief="flat", font=("Segoe UI", 10), padx=16, pady=6,
                  cursor="hand2").pack(side="right", padx=6, pady=8)
        tk.Button(self.approval_bar, text="Reject & Discard", command=lambda:
                  self._answer("abort"), bg=T.BAD, fg=T.BTN_FG, relief="flat",
                  font=("Segoe UI", 10), padx=16, pady=6,
                  cursor="hand2").pack(side="right", padx=6, pady=8)

        # ---- status bar ----
        bar = tk.Frame(self, bg=T.BG)
        bar.pack(fill="x", padx=14, pady=(2, 10))
        self.status_var = tk.StringVar(value="Idle")
        tk.Label(bar, textvariable=self.status_var, bg=T.BG, fg=T.MUTED,
                 font=("Segoe UI", 9), anchor="w").pack(side="left")
        self.pr_btn = tk.Button(bar, text="Open PR", command=self._open_pr,
                                bg=T.PANEL, fg=T.ACCENT, relief="flat",
                                font=("Segoe UI", 9), padx=12, cursor="hand2")

    def _make_text_tab(self, nb: ttk.Notebook, title: str):
        frame = tk.Frame(nb, bg=T.BG)
        nb.add(frame, text=title)
        widget = scrolledtext.ScrolledText(
            frame, bg=T.ENTRY_BG, fg=T.FG, insertbackground=T.FG,
            relief="flat", font=("Consolas", 10), wrap="none", state="disabled",
        )
        widget.pack(fill="both", expand=True, padx=2, pady=2)
        return widget

    # ------------------------------------------------------------------
    # thread-safe UI updates
    # ------------------------------------------------------------------

    def _post(self, fn, *args) -> None:
        self.ui_queue.put((fn, args))

    def _drain_queue(self) -> None:
        try:
            while True:
                fn, args = self.ui_queue.get_nowait()
                try:
                    fn(*args)
                except Exception as exc:  # never let a UI error kill the loop
                    print("UI update failed:", exc)
        except queue.Empty:
            pass
        self.after(60, self._drain_queue)

    def _append(self, widget: tk.Text, text: str, tag: str | None = None) -> None:
        widget.configure(state="normal")
        widget.insert("end", text, tag or ())
        widget.see("end")
        widget.configure(state="disabled")

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.see("1.0")
        widget.configure(state="disabled")

    # ------------------------------------------------------------------
    # hook implementations (called from the worker thread)
    # ------------------------------------------------------------------

    def _hook_step(self, idx: int, name: str, state: str) -> None:
        def apply() -> None:
            icon, role = STATE_ICON[state]
            self.step_labels[idx].configure(
                text="{}  {}. {}".format(icon, idx + 1, name),
                fg=getattr(T, role),
            )
            self._step_states[idx] = state
            self.progress["value"] = sum(
                1 for st in self._step_states.values()
                if st in (pipeline.DONE, pipeline.SKIPPED)
            )
            if state == pipeline.RUNNING:
                self.status_var.set(name + "...")
        self._post(apply)

    def _hook_log(self, msg: str) -> None:
        self._post(self._append, self.log_text, msg + "\n")

    def _hook_issue(self, issue: jira.Issue) -> None:
        def apply() -> None:
            self.summary_var.set("{}  -  {}".format(issue.key, issue.summary))
            self._set_text(self.issue_text, issue.as_prompt_context())
        self._post(apply)

    def _hook_diff(self, diff: str, stat: str) -> None:
        def apply() -> None:
            self.diff_text.configure(state="normal")
            self.diff_text.delete("1.0", "end")
            if stat:
                self.diff_text.insert("end", stat + "\n\n", "head")
            for line in diff.splitlines(keepends=True):
                if (line.startswith("+++") or line.startswith("---")
                        or line.startswith("diff ") or line.startswith("@@")):
                    tag = "head"
                elif line.startswith("+"):
                    tag = "add"
                elif line.startswith("-"):
                    tag = "del"
                else:
                    tag = None
                self.diff_text.insert("end", line, tag or ())
            self.diff_text.see("1.0")
            self.diff_text.configure(state="disabled")
            self.notebook.select(2)
            self.approval_msg.configure(text="Review the diff, then choose:")
            self.approval_bar.pack(fill="x", padx=14, pady=(0, 6),
                                   before=self.pr_btn.master)
            self.bell()
        self._post(apply)

    def _hook_finish(self, ok: bool, msg: str) -> None:
        def apply() -> None:
            self.approval_bar.pack_forget()
            self.run_btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")
            self.status_var.set(msg if ok else "Stopped: " + msg)
            if ok and self.pipeline and self.pipeline.result.pr_url:
                self.pr_url = self.pipeline.result.pr_url
                self.pr_btn.pack(side="right")
                messagebox.showinfo("ACE DEVELOPER AGENT", msg)
            elif not ok:
                messagebox.showerror("ACE DEVELOPER AGENT", msg)
        self._post(apply)

    def _hook_approval(self) -> str:
        """Blocks the worker thread until a button is pressed."""
        return self.approval_queue.get()

    def _answer(self, decision: str) -> None:
        self.approval_bar.pack_forget()
        self.status_var.set({"approve": "Approved - committing...",
                             "regenerate": "Regenerating...",
                             "abort": "Discarding changes..."}[decision])
        self.approval_queue.put(decision)

    # ------------------------------------------------------------------
    # terminal
    # ------------------------------------------------------------------

    def _build_terminal_tab(self, nb: ttk.Notebook) -> None:
        frame = tk.Frame(nb, bg=T.BG)
        nb.add(frame, text="Terminal")

        bar = tk.Frame(frame, bg=T.BG)
        bar.pack(fill="x", padx=4, pady=(6, 2))
        tk.Label(bar, text="Quick:", bg=T.BG, fg=T.MUTED,
                 font=("Segoe UI", 9)).pack(side="left", padx=(4, 6))
        for label, cmd in (
            ("Sign in to GitHub", GH_LOGIN_CMD),
            ("GitHub status", "gh auth status"),
            ("git status", "git status"),
            ("Recent commits", "git log --oneline -n 15"),
        ):
            tk.Button(
                bar, text=label, bg=T.PANEL, fg=T.FG, relief="flat",
                font=("Segoe UI", 9), padx=10, pady=3, cursor="hand2",
                command=lambda c=cmd: self.run_terminal_command(c),
            ).pack(side="left", padx=3)

        self.term_stop_btn = tk.Button(
            bar, text="Stop", bg=T.BAD, fg=T.BTN_FG, relief="flat",
            font=("Segoe UI", 9), padx=10, pady=3, cursor="hand2",
            command=self.stop_terminal, state="disabled",
        )
        self.term_stop_btn.pack(side="right", padx=4)

        self.term_text = scrolledtext.ScrolledText(
            frame, bg=T.ENTRY_BG, fg=T.FG, insertbackground=T.FG,
            relief="flat", font=("Consolas", 10), wrap="word", state="disabled",
        )
        self.term_text.pack(fill="both", expand=True, padx=4, pady=4)
        self.term_text.tag_configure("cmd", foreground=T.ACCENT,
                                     font=("Consolas", 10, "bold"))
        self.term_text.tag_configure("ok", foreground=T.OK)
        self.term_text.tag_configure("err", foreground=T.BAD)
        self.term_text.tag_configure("hint", foreground=T.MUTED)

        entry_row = tk.Frame(frame, bg=T.BG)
        entry_row.pack(fill="x", padx=4, pady=(0, 6))
        tk.Label(entry_row, text=">", bg=T.BG, fg=T.ACCENT,
                 font=("Consolas", 12, "bold")).pack(side="left", padx=(4, 4))

        self.term_var = tk.StringVar()
        self.term_entry = tk.Entry(
            entry_row, textvariable=self.term_var, bg=T.ENTRY_BG, fg=T.FG,
            insertbackground=T.FG, relief="flat", font=("Consolas", 11),
        )
        self.term_entry.pack(side="left", fill="x", expand=True, ipady=5,
                             ipadx=6)
        self.term_entry.bind("<Return>", lambda e: self._submit_terminal())
        self.term_entry.bind("<Up>", lambda e: self._history(-1))
        self.term_entry.bind("<Down>", lambda e: self._history(1))

        tk.Button(entry_row, text="Run", command=self._submit_terminal,
                  bg=T.ACCENT, fg=T.BTN_FG, relief="flat", padx=16, pady=5,
                  font=("Segoe UI", 9, "bold"), cursor="hand2").pack(
            side="left", padx=(6, 4))

        self._term_history: list[str] = []
        self._term_hist_pos = 0
        self.term_session: terminal.TerminalSession | None = None

        if not terminal.HAVE_PTY:
            self._term_write(
                "Note: pywinpty is not installed, so interactive commands "
                "(like `gh auth login`) may not work here.\n"
                "Install it with:  pip install pywinpty\n\n", "hint")
        self._term_write(
            "Type a command and press Enter. While something is running, "
            "what you type is sent to it as input.\n\n", "hint")

    def _term_write(self, text: str, tag: str | None = None) -> None:
        self.term_text.configure(state="normal")
        self.term_text.insert("end", text, tag or ())
        self.term_text.see("end")
        self.term_text.configure(state="disabled")

    def _history(self, delta: int) -> str:
        if not self._term_history:
            return "break"
        self._term_hist_pos = max(
            0, min(len(self._term_history), self._term_hist_pos + delta))
        if self._term_hist_pos < len(self._term_history):
            self.term_var.set(self._term_history[self._term_hist_pos])
        else:
            self.term_var.set("")
        self.term_entry.icursor("end")
        return "break"

    def _submit_terminal(self) -> None:
        text = self.term_var.get()
        self.term_var.set("")
        session = self.term_session
        if session is not None and session.running:
            # feed the running command - this is what makes `gh auth login` work
            self._term_write(text + "\n", "cmd")
            session.send(text)
            return
        if text.strip():
            self.run_terminal_command(text.strip())

    def run_terminal_command(self, command: str) -> None:
        if self.term_session is not None and self.term_session.running:
            self._term_write("A command is still running. Stop it first.\n",
                             "err")
            return

        self.notebook.select(self.notebook.index("end") - 1)
        self._term_history.append(command)
        self._term_hist_pos = len(self._term_history)
        self._term_write("\n> " + command + "\n", "cmd")
        self.term_stop_btn.configure(state="normal")

        # the GitHub token from Settings, so `gh` works without a login
        env = {}
        if self.cfg.github_token:
            env["GH_TOKEN"] = self.cfg.github_token
            env["GITHUB_TOKEN"] = self.cfg.github_token

        self.term_session = terminal.TerminalSession(
            on_output=lambda line: self._post(self._term_write, line + "\n"),
            on_exit=self._on_terminal_exit,
            cwd=self.cfg.repo_path or ".",
            env=env or None,
        )
        try:
            self.term_session.start(command)
        except Exception as exc:
            self._term_write("Could not start: {}\n".format(exc), "err")
            self.term_stop_btn.configure(state="disabled")

    def _on_terminal_exit(self, code) -> None:
        def apply() -> None:
            tag = "ok" if code == 0 else "err"
            self._term_write("[finished, exit {}]\n".format(code), tag)
            self.term_stop_btn.configure(state="disabled")
            self.term_entry.focus_set()
        self._post(apply)

    def stop_terminal(self) -> None:
        if self.term_session is not None:
            self.term_session.stop()
            self._term_write("[stopped]\n", "err")
            self.term_stop_btn.configure(state="disabled")

    # ------------------------------------------------------------------
    # GitHub auth
    # ------------------------------------------------------------------

    def _check_github_auth(self) -> None:
        """Warn once at startup if the PR step would fail, and offer the fix.

        Running `gh auth login` unconditionally on every launch would open a
        browser each time even when already signed in, so this only prompts
        when authentication is actually missing.
        """
        def probe() -> None:
            repo = self.cfg.repo_path or "."
            try:
                if not vcs.gh_available(repo):
                    self._post(self.status_var.set,
                               "GitHub CLI (gh) was not found on PATH - the "
                               "Create PR step cannot run.")
                elif vcs.gh_authenticated(repo, self.cfg.github_token):
                    who = vcs.gh_login_name(repo, self.cfg.github_token)
                    self._post(self.status_var.set,
                               "GitHub: signed in"
                               + (" as " + who if who else ""))
                elif self.cfg.github_token:
                    self._post(self.status_var.set,
                               "The GitHub token in Settings was rejected - "
                               "check it has not expired.")
                else:
                    self._post(self._offer_github_login)
            except Exception:
                return

        threading.Thread(target=probe, daemon=True).start()

    def _offer_github_login(self) -> None:
        self.status_var.set(
            "GitHub is not signed in - the Create PR step will fail.")
        if messagebox.askyesno(
            "ACE DEVELOPER AGENT",
            "GitHub is not signed in, so the Create PR step would fail.\n\n"
            "Sign in now? This opens your browser and shows a one-time code "
            "in the Terminal tab.\n\n"
            "(You can also paste a GitHub token in Settings instead.)",
        ):
            self.run_terminal_command(GH_LOGIN_CMD)

    # ------------------------------------------------------------------
    # theme
    # ------------------------------------------------------------------

    def _sync_theme_button(self) -> None:
        """Label the button with the theme it will switch to."""
        self.theme_btn.configure(
            text="Light mode" if T.name == "dark" else "Dark mode")

    def toggle_theme(self) -> None:
        target = T.other()
        apply_theme(self, target)
        for win in self.winfo_children():
            if isinstance(win, tk.Toplevel):
                style_ttk(win)
        self._sync_theme_button()
        self._recolor_steps()
        cfgmod.save({"theme": target})
        self.cfg = cfgmod.load()

    def _recolor_steps(self) -> None:
        """Step labels are coloured by state, which the tree walk preserves."""
        for lbl in self.step_labels:
            text = lbl.cget("text")
            for state, (icon, role) in STATE_ICON.items():
                if text.startswith(icon):
                    lbl.configure(fg=getattr(T, role))
                    break

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        key = self.issue_var.get().strip()
        if not key or key == "ACE-":
            messagebox.showwarning(
                "ACE DEVELOPER AGENT", "Enter a Jira issue key, e.g. ACE-24308.")
            return

        self.cfg = cfgmod.load()
        hooks = pipeline.Hooks(
            on_step=self._hook_step,
            on_log=self._hook_log,
            on_issue=self._hook_issue,
            on_diff=self._hook_diff,
            on_finish=self._hook_finish,
            ask_approval=self._hook_approval,
        )
        self.pipeline = pipeline.Pipeline(
            self.cfg, hooks, self.extra_text.get("1.0", "end").strip())

        problems = self.pipeline.preflight(key)
        if problems:
            messagebox.showerror(
                "Cannot start", "Fix these first:\n\n- " + "\n- ".join(problems))
            return

        self._set_text(self.log_text, "")
        self._set_text(self.diff_text, "")
        self.pr_btn.pack_forget()
        self.pipeline.reset_steps()
        self.progress["value"] = 0
        self.run_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")

        # drain any stale approval answers from a previous run
        while not self.approval_queue.empty():
            self.approval_queue.get_nowait()

        self.worker = threading.Thread(
            target=self._run_with_retries, args=(key,), daemon=True)
        self.worker.start()

    def _run_with_retries(self, key: str, max_attempts: int = 4) -> None:
        """Run the pipeline, restarting when the user asks to regenerate."""
        for attempt in range(1, max_attempts + 1):
            try:
                assert self.pipeline is not None
                self.pipeline.run(key)
                return
            except pipeline.Regenerate:
                if attempt == max_attempts:
                    self._hook_finish(
                        False, "Gave up after {} attempts.".format(attempt))
                    return
                self._hook_log(
                    "\n--- Regenerating (attempt {}) ---".format(attempt + 1))
                vcs.discard_all_changes(self.cfg.repo_path)
                self.pipeline.reset_steps()

    def cancel(self) -> None:
        if self.pipeline:
            self.pipeline.cancel()
            self.status_var.set("Cancelling after the current step...")
        # unblock the approval gate if we are sitting on it
        self.approval_queue.put("abort")

    def _open_pr(self) -> None:
        if self.pr_url:
            webbrowser.open(self.pr_url)

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askokcancel(
                "ACE DEVELOPER AGENT", "A run is in progress. Quit anyway?"
            ):
                return
            if self.pipeline:
                self.pipeline.cancel()
            self.approval_queue.put("abort")
        if self.term_session is not None:
            self.term_session.stop()
        self.destroy()

    def open_settings(self) -> None:
        SettingsDialog(self)


def main() -> None:
    App().mainloop()
