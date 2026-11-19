"""Shared visual tokens and ttk component styles for duomiT Desktop.

The Desktop is intentionally a restrained local productivity tool.  These
tokens are the single source of truth for native shells; feature views do not
introduce ad-hoc colors, radii, or button treatments.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class DesktopTokens:
    canvas = "#F7F8F6"
    surface = "#FFFFFF"
    surface_subtle = "#F1F4F2"
    ink = "#17211E"
    muted = "#5D6A65"
    line = "#D6DDD8"
    primary = "#0F766E"
    primary_pressed = "#115E59"
    primary_soft = "#DDF2EE"
    attention = "#B45309"
    attention_soft = "#FFF4E5"
    success = "#166534"
    success_soft = "#EAF6ED"
    danger = "#B42318"
    danger_soft = "#FCEBE9"
    focus = "#0F766E"
    font = "Segoe UI Variable"
    fallback_font = "Segoe UI"


def configure_desktop_style(root: tk.Misc) -> ttk.Style:
    """Install consistent native component styles on one Tk interpreter."""
    tokens = DesktopTokens
    root.configure(bg=tokens.canvas)
    style = ttk.Style(root)
    style.theme_use("clam")
    base = (tokens.font, 10)
    style.configure("App.TFrame", background=tokens.canvas)
    style.configure("Rail.TFrame", background=tokens.surface)
    style.configure("Surface.TFrame", background=tokens.surface)
    style.configure("Subtle.TFrame", background=tokens.surface_subtle)
    style.configure("Title.TLabel", background=tokens.canvas, foreground=tokens.ink, font=(tokens.font, 22, "bold"))
    style.configure("PageTitle.TLabel", background=tokens.surface, foreground=tokens.ink, font=(tokens.font, 15, "bold"))
    style.configure("Section.TLabel", background=tokens.surface, foreground=tokens.ink, font=(tokens.font, 11, "bold"))
    style.configure("Body.TLabel", background=tokens.surface, foreground=tokens.ink, font=base)
    style.configure("Muted.TLabel", background=tokens.surface, foreground=tokens.muted, font=(tokens.font, 9))
    style.configure("CanvasMuted.TLabel", background=tokens.canvas, foreground=tokens.muted, font=(tokens.font, 9))
    style.configure("RailBrand.TLabel", background=tokens.surface, foreground=tokens.ink, font=(tokens.font, 16, "bold"))
    style.configure("RailMuted.TLabel", background=tokens.surface, foreground=tokens.muted, font=(tokens.font, 9))
    style.configure("Primary.TButton", background=tokens.primary, foreground="#FFFFFF", borderwidth=0,
                    focusthickness=2, focuscolor=tokens.focus, padding=(12, 7), font=(tokens.font, 9, "bold"))
    style.map("Primary.TButton", background=[("active", tokens.primary_pressed), ("pressed", tokens.primary_pressed), ("disabled", tokens.line)],
              foreground=[("disabled", tokens.muted)])
    style.configure("Secondary.TButton", background=tokens.surface, foreground=tokens.ink, borderwidth=1,
                    relief="solid", padding=(10, 6), font=(tokens.font, 9))
    style.map("Secondary.TButton", background=[("active", tokens.surface_subtle), ("pressed", tokens.primary_soft), ("disabled", tokens.canvas)],
              foreground=[("disabled", tokens.muted)])
    style.configure("Quiet.TButton", background=tokens.surface, foreground=tokens.primary, borderwidth=0,
                    padding=(6, 4), font=(tokens.font, 9))
    style.map("Quiet.TButton", background=[("active", tokens.primary_soft), ("pressed", tokens.primary_soft)])
    style.configure("Treeview", background=tokens.surface, foreground=tokens.ink, fieldbackground=tokens.surface,
                    borderwidth=0, rowheight=36, font=base)
    style.configure("Treeview.Heading", background=tokens.surface_subtle, foreground=tokens.muted,
                    borderwidth=0, relief="flat", font=(tokens.font, 9, "bold"), padding=(10, 8))
    style.map("Treeview", background=[("selected", tokens.primary_soft)], foreground=[("selected", tokens.ink)])
    style.configure("TEntry", fieldbackground=tokens.surface, foreground=tokens.ink, bordercolor=tokens.line,
                    lightcolor=tokens.line, darkcolor=tokens.line, padding=(8, 6))
    style.map("TEntry", bordercolor=[("focus", tokens.focus)], lightcolor=[("focus", tokens.focus)], darkcolor=[("focus", tokens.focus)])
    return style


def status_palette(status: str) -> tuple[str, str]:
    if status in ("valid", "sufficient", "verified"):
        return DesktopTokens.success, DesktopTokens.success_soft
    if status in ("invalid", "insufficient", "failed"):
        return DesktopTokens.danger, DesktopTokens.danger_soft
    if status in ("review", "attention", "waiting"):
        return DesktopTokens.attention, DesktopTokens.attention_soft
    return DesktopTokens.muted, DesktopTokens.surface_subtle
