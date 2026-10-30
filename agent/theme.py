"""Colour palettes and runtime theme switching.

Widgets read colours through the live `T` object rather than importing the
values, so a theme change is picked up by anything built afterwards. Widgets
that already exist are recoloured by `apply_theme`, which walks the widget
tree and swaps any colour it recognises from the outgoing palette.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

ROLES = (
    "BG", "PANEL", "FG", "MUTED", "ACCENT", "OK", "BAD", "WARN",
    "ENTRY_BG", "SELECT", "DIFF_ADD", "DIFF_DEL", "DIFF_HEAD", "BTN_FG",
)

DARK = {
    "BG": "#1e1f23",
    "PANEL": "#26272c",
    "FG": "#e6e6e6",
    "MUTED": "#9aa0a6",
    "ACCENT": "#4c8dff",
    "OK": "#39b56a",
    "BAD": "#e5534b",
    "WARN": "#d8a23a",
    "ENTRY_BG": "#26272c",
    "SELECT": "#33363d",
    "DIFF_ADD": "#2ea043",
    "DIFF_DEL": "#f85149",
    "DIFF_HEAD": "#58a6ff",
    "BTN_FG": "#ffffff",
}

LIGHT = {
    "BG": "#f6f7f9",
    "PANEL": "#ffffff",
    "FG": "#1c1e21",
    "MUTED": "#5f6368",
    "ACCENT": "#1a73e8",
    "OK": "#16794a",
    "BAD": "#c5221f",
    "WARN": "#8a5a00",
    "ENTRY_BG": "#ffffff",
    "SELECT": "#dde3ea",
    "DIFF_ADD": "#0f7b33",
    "DIFF_DEL": "#c5221f",
    "DIFF_HEAD": "#0b57d0",
    "BTN_FG": "#ffffff",
}

PALETTES = {"dark": DARK, "light": LIGHT}

COLOR_OPTIONS = (
    "background", "foreground", "selectcolor", "insertbackground",
    "activebackground", "activeforeground", "troughcolor",
    "highlightbackground", "highlightcolor", "disabledforeground",
    "readonlybackground",
)


class Palette:
    """Live palette. Attribute reads always return the current theme."""

    def __init__(self, name: str = "dark") -> None:
        self.name = name
        self._apply(name)

    def _apply(self, name: str) -> None:
        self.name = name
        for role, value in PALETTES[name].items():
            setattr(self, role, value)

    def set(self, name: str) -> None:
        if name not in PALETTES:
            raise ValueError("unknown theme: " + name)
        self._apply(name)

    def other(self) -> str:
        return "light" if self.name == "dark" else "dark"


T = Palette("dark")


def _swap_map(old: str, new: str) -> dict[str, str]:
    """Map every colour in the outgoing palette to the incoming equivalent."""
    out: dict[str, str] = {}
    for role in ROLES:
        out[PALETTES[old][role].lower()] = PALETTES[new][role]
    return out


def apply_theme(root: tk.Misc, new_name: str) -> None:
    """Switch `root` and every descendant to the named theme."""
    old_name = T.name
    if old_name == new_name:
        return
    swap = _swap_map(old_name, new_name)
    T.set(new_name)
    _restyle(root, swap)
    style_ttk(root)


def _restyle(widget: tk.Misc, swap: dict[str, str]) -> None:
    try:
        keys = widget.keys()
    except Exception:
        keys = []

    for opt in COLOR_OPTIONS:
        if opt not in keys:
            continue
        try:
            current = str(widget.cget(opt)).lower()
        except Exception:
            continue
        if current in swap:
            try:
                widget.configure(**{opt: swap[current]})
            except Exception:
                pass

    # Text widgets carry their own tag colours (the diff highlighting).
    if isinstance(widget, tk.Text):
        for tag in widget.tag_names():
            for opt in ("foreground", "background"):
                try:
                    current = str(widget.tag_cget(tag, opt)).lower()
                except Exception:
                    continue
                if current in swap:
                    try:
                        widget.tag_configure(tag, **{opt: swap[current]})
                    except Exception:
                        pass

    for child in widget.winfo_children():
        _restyle(child, swap)


def style_ttk(root: tk.Misc) -> None:
    """Restyle the ttk widgets, which ignore plain tk colour options."""
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TNotebook", background=T.BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=T.PANEL, foreground=T.FG,
                    padding=(14, 7))
    style.map("TNotebook.Tab",
              background=[("selected", T.BG)],
              foreground=[("selected", T.ACCENT)])
    style.configure("TProgressbar", background=T.ACCENT, troughcolor=T.PANEL,
                    borderwidth=0)
    style.configure("TCombobox", fieldbackground=T.ENTRY_BG, background=T.PANEL,
                    foreground=T.FG, arrowcolor=T.FG)
    style.map("TCombobox",
              fieldbackground=[("readonly", T.ENTRY_BG)],
              foreground=[("readonly", T.FG)],
              selectbackground=[("readonly", T.ENTRY_BG)],
              selectforeground=[("readonly", T.FG)])
    try:
        root.option_add("*TCombobox*Listbox.background", T.ENTRY_BG)
        root.option_add("*TCombobox*Listbox.foreground", T.FG)
        root.option_add("*TCombobox*Listbox.selectBackground", T.ACCENT)
        root.option_add("*TCombobox*Listbox.selectForeground", T.BTN_FG)
    except Exception:
        pass
