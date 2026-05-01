#!/usr/bin/env python3
"""
Internet Limiter — desktop GUI (CustomTkinter) for Windows and macOS.
Windows build: build_windows.bat. macOS build: build_macos.sh.
"""

from __future__ import annotations

import ctypes
import json
import platform
import queue
import subprocess
import sys
import threading
import tkinter as tk
from collections import deque
from tkinter import messagebox
from typing import Any

import customtkinter as ctk

from limiter_core import NetworkLimiter

APP_NAME = "Internet Limiter"
# Limit-hit alert: keep to a few words only
LIMIT_ALERT_TITLE = "Limit reached"
LIMIT_ALERT_HINT = "v2rayN was closed"
SUPPORTED_GUI_PLATFORMS = frozenset({"Windows", "Darwin"})

# Surfaces (light, dark) — tuned for readability and a calm “dashboard” feel
CARD_FG = ("#ffffff", "#1e2128")
CARD_BORDER = ("#e2e5eb", "#2d323c")
MUTED_TEXT = ("#5c6570", "#9aa3b2")
SUBTLE_BG = ("#f4f6f9", "#14161a")
ACCENT = "#3b82f6"
WARN = "#f59e0b"

CHART_MAX_POINTS = 120
CHART_IDLE_TEXT = "Start monitoring to see usage vs. limit"


def _resolved_apps_light_theme() -> bool:
    """Best-effort: actual light/dark when appearance is System (Windows)."""
    if platform.system() != "Windows":
        return False
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 1
        finally:
            winreg.CloseKey(key)
    except OSError:
        return False


def _play_limit_reached_alert() -> None:
    """Alert so users notice the usage limit was hit."""
    if platform.system() == "Windows":
        import winsound

        for alias in ("SystemHand", "SystemExclamation"):
            try:
                winsound.PlaySound(
                    alias,
                    winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
                return
            except RuntimeError:
                continue
        try:
            winsound.MessageBeep(0x30)  # MB_ICONWARNING
        except RuntimeError:
            pass
        return

    if platform.system() == "Darwin":
        import os

        for path in (
            "/System/Library/Sounds/Basso.aiff",
            "/System/Library/Sounds/Sosumi.aiff",
        ):
            if os.path.isfile(path):
                subprocess.Popen(
                    ["/usr/bin/afplay", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return


def _macos_notify_internet_blocked() -> None:
    script = (
        f"display notification {json.dumps(LIMIT_ALERT_HINT)} "
        f"with title {json.dumps(LIMIT_ALERT_TITLE)}"
    )
    try:
        subprocess.Popen(
            ["/usr/bin/osascript", "-e", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def _flash_window_taskbar(ctk_window: ctk.CTk) -> None:
    if platform.system() != "Windows":
        return
    try:
        hwnd = ctypes.c_void_p(int(ctk_window.winfo_id()))

        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("hwnd", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint),
                ("uCount", ctypes.c_uint),
                ("dwTimeout", ctypes.c_uint),
            ]

        FLASHW_ALL = 3
        info = FLASHWINFO()
        info.cbSize = ctypes.sizeof(FLASHWINFO)
        info.hwnd = hwnd
        info.dwFlags = FLASHW_ALL
        info.uCount = 3
        info.dwTimeout = 0
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception:
        pass


def _parse_float_entry(entry: ctk.CTkEntry, default: float) -> float:
    s = entry.get().strip()
    if not s:
        return default
    return float(s)


class InternetLimiterApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        ctk.set_default_color_theme("blue")

        self.title(APP_NAME)
        self.minsize(460, 640)
        ww, hh = 520, 820
        self.geometry(f"{ww}x{hh}")
        self.configure(fg_color=SUBTLE_BG)

        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self._ui_queue: queue.Queue = queue.Queue()
        self._limiter = NetworkLimiter()
        self._chart_samples: deque[float] = deque(maxlen=CHART_MAX_POINTS)
        self._chart_canvas: tk.Canvas | None = None
        self._chart_redraw_after: str | None = None
        self._stop_busy = False
        self._limit_dialog_open = False

        self._build_ui()
        self.update_idletasks()
        x = max(0, (self.winfo_screenwidth() - ww) // 2)
        y = max(0, (self.winfo_screenheight() - hh) // 2)
        self.geometry(f"{ww}x{hh}+{x}+{y}")

        self.after(100, self._drain_ui_queue)

    def _card(self, parent: Any, **kwargs: Any) -> ctk.CTkFrame:
        return ctk.CTkFrame(
            parent,
            fg_color=CARD_FG,
            corner_radius=14,
            border_width=1,
            border_color=CARD_BORDER,
            **kwargs,
        )

    def _build_ui(self) -> None:
        root = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_button_color=("#c5cad3", "#3d4450"),
            scrollbar_button_hover_color=("#aeb4bf", "#525a68"),
        )
        root.pack(fill="both", expand=True, padx=20, pady=(20, 12))

        # —— Header ——
        head = ctk.CTkFrame(root, fg_color="transparent")
        head.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            head,
            text=APP_NAME,
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"),
            text_color=("#111827", "#f3f4f6"),
        ).pack(anchor="w")

        # —— Theme ——
        theme_row = ctk.CTkFrame(head, fg_color="transparent")
        theme_row.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(
            theme_row,
            text="Appearance",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=MUTED_TEXT,
        ).pack(side="left")
        self._theme_seg = ctk.CTkSegmentedButton(
            theme_row,
            values=["Dark", "Light", "System"],
            command=self._on_theme_change,
            font=ctk.CTkFont(size=12),
            height=32,
            corner_radius=8,
        )
        self._theme_seg.pack(side="right")
        self._theme_seg.set("Dark")
        ctk.set_appearance_mode("dark")

        # —— Limits card ——
        limits = self._card(root)
        limits.pack(fill="x", pady=(18, 0))
        inner = ctk.CTkFrame(limits, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=16)

        ctk.CTkLabel(
            inner,
            text="Limits",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=("#111827", "#f9fafb"),
        ).pack(anchor="w")

        self.ent_threshold = self._labeled_entry(
            inner,
            "Max usage in window (MB)",
            "20",
            "Total download+upload allowed before v2rayN is closed.",
        )
        self.ent_window = self._labeled_entry(
            inner,
            "Rolling window (minutes)",
            "5",
            "Usage is measured over this sliding period.",
        )
        self.ent_interval = self._labeled_entry(
            inner,
            "Sample interval (seconds)",
            "2",
            "How often to read network counters (lower = smoother UI, slightly more CPU).",
        )

        # —— Actions ——
        actions = ctk.CTkFrame(root, fg_color="transparent")
        actions.pack(fill="x", pady=(16, 0))

        row = ctk.CTkFrame(actions, fg_color="transparent")
        row.pack(fill="x")

        self.btn_start = ctk.CTkButton(
            row,
            text="Start monitoring",
            command=self._on_start,
            height=44,
            corner_radius=10,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=ACCENT,
            hover_color="#2563eb",
        )
        self.btn_start.pack(side="left", expand=True, fill="x", padx=(0, 8))

        self.btn_stop = ctk.CTkButton(
            row,
            text="Stop",
            command=self._on_stop,
            height=44,
            corner_radius=10,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=("gray82", "gray28"),
            hover_color=("gray72", "gray38"),
            state="disabled",
        )
        self.btn_stop.pack(side="left", expand=True, fill="x", padx=(8, 0))

        # —— Status card ——
        status = self._card(root)
        status.pack(fill="x", pady=(18, 0))
        sinner = ctk.CTkFrame(status, fg_color="transparent")
        sinner.pack(fill="x", padx=18, pady=16)

        ctk.CTkLabel(
            sinner,
            text="Live status",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=("#111827", "#f9fafb"),
        ).pack(anchor="w")

        self.lbl_usage = ctk.CTkLabel(
            sinner,
            text="Usage in window —",
            font=ctk.CTkFont(family="Segoe UI", size=14, weight="bold"),
            text_color=("#1f2937", "#e5e7eb"),
            anchor="w",
        )
        self.lbl_usage.pack(fill="x", pady=(10, 4))

        chart_shell = ctk.CTkFrame(
            sinner,
            fg_color=("gray98", "#22252d"),
            corner_radius=12,
            border_width=1,
            border_color=CARD_BORDER,
        )
        chart_shell.pack(fill="x", pady=(6, 10))
        self._chart_canvas = tk.Canvas(
            chart_shell,
            height=210,
            highlightthickness=0,
            borderwidth=0,
        )
        self._chart_canvas.pack(fill="x", expand=True, padx=2, pady=2)
        self._chart_canvas.bind("<Configure>", self._schedule_chart_redraw)

        self.progress = ctk.CTkProgressBar(
            sinner,
            height=14,
            corner_radius=7,
            progress_color=ACCENT,
            fg_color=("#e5e7eb", "#374151"),
        )
        self.progress.pack(fill="x", pady=(0, 12))
        self.progress.set(0)

        badge_row = ctk.CTkFrame(sinner, fg_color="transparent")
        badge_row.pack(fill="x")
        self._badge = ctk.CTkFrame(
            badge_row,
            fg_color=("#ecfdf5", "#14532d"),
            corner_radius=20,
            height=36,
        )
        self._badge.pack(anchor="w")
        self._badge.pack_propagate(False)
        self.lbl_blocked = ctk.CTkLabel(
            self._badge,
            text="  v2rayN: not closed by limiter  ",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=("#166534", "#bbf7d0"),
        )
        self.lbl_blocked.pack(expand=True)

        # —— Log ——
        log_card = self._card(root)
        log_card.pack(fill="both", expand=True, pady=(18, 8))
        log_inner = ctk.CTkFrame(log_card, fg_color="transparent")
        log_inner.pack(fill="both", expand=True, padx=14, pady=(14, 14))

        ctk.CTkLabel(
            log_inner,
            text="Activity log",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=("#111827", "#f9fafb"),
        ).pack(anchor="w", pady=(0, 8))

        self.txt_log = ctk.CTkTextbox(
            log_inner,
            height=160,
            corner_radius=10,
            font=ctk.CTkFont(family="Consolas", size=12),
            border_width=1,
            border_color=CARD_BORDER,
            fg_color=("#fafafa", "#252830"),
            text_color=("#111827", "#e5e7eb"),
        )
        self.txt_log.pack(fill="both", expand=True)
        self.txt_log.configure(state="disabled")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._draw_usage_chart)

    def _chart_uses_light_palette(self) -> bool:
        mode = ctk.get_appearance_mode()
        if mode == "Light":
            return True
        if mode == "Dark":
            return False
        return _resolved_apps_light_theme()

    def _chart_palette(self) -> dict[str, str]:
        if self._chart_uses_light_palette():
            return {
                "bg": "#f3f4f6",
                "grid": "#d1d5db",
                "fill": "#93c5fd",
                "fill_outline": "#60a5fa",
                "line": "#1d4ed8",
                "limit": "#ea580c",
                "limit_text": "#9a3412",
                "muted": "#6b7280",
                "idle": "#9ca3af",
            }
        return {
            "bg": "#1a1d24",
            "grid": "#3f4654",
            "fill": "#1e3a5f",
            "fill_outline": "#3b82f6",
            "line": "#93c5fd",
            "limit": "#fbbf24",
            "limit_text": "#fcd34d",
            "muted": "#9ca3af",
            "idle": "#6b7280",
        }

    def _schedule_chart_redraw(self, _event: Any = None) -> None:
        if self._chart_redraw_after is not None:
            try:
                self.after_cancel(self._chart_redraw_after)
            except (tk.TclError, ValueError):
                pass
        self._chart_redraw_after = self.after(120, self._draw_usage_chart)

    def _draw_usage_chart(self) -> None:
        self._chart_redraw_after = None
        c = self._chart_canvas
        if c is None:
            return

        pal = self._chart_palette()
        w = max(80, c.winfo_width())
        h = max(60, c.winfo_height())
        c.delete("all")
        c.configure(bg=pal["bg"])

        m_left, m_right, m_top, m_bottom = 44, 14, 16, 32
        x0, y0 = m_left, m_top
        x1, y1 = w - m_right, h - m_bottom
        if x1 <= x0 + 8 or y1 <= y0 + 8:
            return

        # Grid & Y labels (0 / 50 / 100%)
        for pct, label in ((0, "0%"), (50, "50%"), (100, "100%")):
            yy = y0 + (1.0 - pct / 100.0) * (y1 - y0)
            c.create_line(x0, yy, x1, yy, fill=pal["grid"], dash=(4, 6) if pct else ())
            c.create_text(x0 - 8, yy, text=label, anchor="e", fill=pal["muted"], font=("Segoe UI", 9))

        c.create_text((x0 + x1) / 2, h - 10, text="Recent samples →", fill=pal["muted"], font=("Segoe UI", 9))

        samples = list(self._chart_samples)
        if not samples:
            c.create_text(
                (x0 + x1) / 2,
                (y0 + y1) / 2,
                text=CHART_IDLE_TEXT,
                fill=pal["idle"],
                font=("Segoe UI", 11),
            )
            return

        y_max = max(110.0, max(samples) * 1.08, 100.0)

        # 100% limit line (block threshold)
        y_lim = y0 + (1.0 - 100.0 / y_max) * (y1 - y0)
        c.create_line(x0, y_lim, x1, y_lim, fill=pal["limit"], width=2, dash=(6, 4))
        c.create_text(x1 - 2, y_lim - 8, text="Limit", anchor="ne", fill=pal["limit_text"], font=("Segoe UI", 9, "bold"))

        n = len(samples)
        span = max(1, n - 1)
        pts: list[tuple[float, float]] = []
        for i, val in enumerate(samples):
            xx = x0 + (i / span) * (x1 - x0)
            clipped = max(0.0, min(val, y_max))
            yy = y0 + (1.0 - clipped / y_max) * (y1 - y0)
            pts.append((xx, yy))

        # Area fill (flat x1,y1,x2,y2,…)
        poly_pts = [(x0, y1)] + pts + [(x1, y1)]
        poly_flat: list[float] = []
        for px, py in poly_pts:
            poly_flat.extend((px, py))
        c.create_polygon(*poly_flat, fill=pal["fill"], outline="")

        # Stroke on top of area
        if len(pts) >= 2:
            line_flat: list[float] = []
            for px, py in pts:
                line_flat.extend((px, py))
            c.create_line(
                *line_flat,
                fill=pal["line"],
                width=2,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
                smooth=False,
            )
        elif len(pts) == 1:
            c.create_oval(pts[0][0] - 4, pts[0][1] - 4, pts[0][0] + 4, pts[0][1] + 4, fill=pal["line"], outline="")

    def _labeled_entry(
        self,
        parent: ctk.CTkFrame,
        title: str,
        default: str,
        hint: str,
    ) -> ctk.CTkEntry:
        block = ctk.CTkFrame(parent, fg_color="transparent")
        block.pack(fill="x", pady=(14, 0))

        top = ctk.CTkFrame(block, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top,
            text=title,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=("#374151", "#d1d5db"),
        ).pack(side="left")

        ent = ctk.CTkEntry(
            top,
            width=110,
            height=36,
            corner_radius=8,
            border_color=CARD_BORDER,
            font=ctk.CTkFont(size=13),
            justify="right",
        )
        ent.pack(side="right")
        ent.insert(0, default)

        ctk.CTkLabel(
            block,
            text=hint,
            font=ctk.CTkFont(size=11),
            text_color=MUTED_TEXT,
            wraplength=420,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))

        return ent

    def _on_theme_change(self, value: str) -> None:
        ctk.set_appearance_mode(value.lower())
        self.after(80, self._draw_usage_chart)

    def _notify_limit_reached(self) -> None:
        if self._limit_dialog_open:
            return
        self._limit_dialog_open = True
        _play_limit_reached_alert()
        self.lift()
        self.attributes("-topmost", True)
        self.after(450, lambda: self.attributes("-topmost", False))
        try:
            self.focus_force()
        except tk.TclError:
            pass
        if platform.system() == "Windows":
            _flash_window_taskbar(self)
        if platform.system() == "Darwin":
            _macos_notify_internet_blocked()

        dlg = ctk.CTkToplevel(self)
        dlg.title(LIMIT_ALERT_TITLE)
        dlg.transient(self)
        dlg.resizable(False, False)
        dlg.configure(fg_color=SUBTLE_BG)

        shell = ctk.CTkFrame(
            dlg,
            fg_color=CARD_FG,
            corner_radius=14,
            border_width=1,
            border_color=CARD_BORDER,
        )
        shell.pack(fill="both", expand=True, padx=14, pady=14)

        banner = ctk.CTkFrame(
            shell, fg_color=("#fef2f2", "#7f1d1d"), corner_radius=10
        )
        banner.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            banner,
            text=LIMIT_ALERT_TITLE,
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color=("#b91c1c", "#fecaca"),
        ).pack(padx=14, pady=10)
        ctk.CTkLabel(
            shell,
            text=LIMIT_ALERT_HINT,
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=MUTED_TEXT,
        ).pack(anchor="center", pady=(0, 12))

        def _close() -> None:
            self._limit_dialog_open = False
            try:
                dlg.grab_release()
            except tk.TclError:
                pass
            dlg.destroy()

        btn = ctk.CTkButton(
            shell,
            text="OK",
            command=_close,
            fg_color=WARN,
            hover_color="#d97706",
            width=160,
            height=36,
            corner_radius=10,
            font=ctk.CTkFont(size=14, weight="bold"),
        )
        btn.pack(pady=(0, 8))

        dlg.protocol("WM_DELETE_WINDOW", _close)
        dlg.update_idletasks()
        rw, rh = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - rw) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - rh) // 2)
        dlg.geometry(f"+{x}+{y}")
        dlg.grab_set()
        try:
            dlg.focus_force()
        except tk.TclError:
            pass
        btn.focus_set()

    def _set_badge(self, limit_hit: bool) -> None:
        if limit_hit:
            self._badge.configure(fg_color=("#fef2f2", "#7f1d1d"))
            self.lbl_blocked.configure(
                text="  v2rayN: closed (limit reached)  ",
                text_color=("#b91c1c", "#fecaca"),
            )
        else:
            self._badge.configure(fg_color=("#ecfdf5", "#14532d"))
            self.lbl_blocked.configure(
                text="  v2rayN: not closed by limiter  ",
                text_color=("#166534", "#bbf7d0"),
            )

    def _append_log(self, line: str) -> None:
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", line + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _drain_ui_queue(self) -> None:
        try:
            while True:
                kind, *rest = self._ui_queue.get_nowait()
                if kind == "log":
                    self._append_log(rest[0])
                elif kind == "usage":
                    used, thresh, pct = rest
                    self._chart_samples.append(float(pct))
                    self._schedule_chart_redraw()
                    self.progress.set(min(1.0, max(0.0, pct / 100.0)))
                    self.progress.configure(
                        progress_color=WARN if pct >= 85 else ACCENT
                    )
                    self.lbl_usage.configure(
                        text=(
                            f"{NetworkLimiter.format_bytes(used)}  /  "
                            f"{NetworkLimiter.format_bytes(thresh)}  ·  {pct:.1f}%"
                        )
                    )
                elif kind == "limit_reached":
                    self.after(0, self._notify_limit_reached)
                elif kind == "blocked":
                    self._set_badge(rest[0])
        except queue.Empty:
            pass
        self.after(100, self._drain_ui_queue)

    def _enqueue(self, item: tuple) -> None:
        self._ui_queue.put(item)

    def _on_start(self) -> None:
        if self._worker and self._worker.is_alive():
            return

        try:
            t = _parse_float_entry(self.ent_threshold, 20.0)
            w = _parse_float_entry(self.ent_window, 5.0)
            iv = _parse_float_entry(self.ent_interval, 2.0)
        except ValueError:
            messagebox.showerror(APP_NAME, "Enter valid numbers for limits.")
            return

        if t <= 0 or w <= 0 or iv <= 0:
            messagebox.showerror(APP_NAME, "Threshold, window, and interval must be positive.")
            return

        self._stop.clear()
        self._limiter = NetworkLimiter(threshold_mb=t, window_minutes=w, check_interval=iv)

        def log_fn(msg: str) -> None:
            self._enqueue(("log", msg))

        def usage_fn(used: int, thresh: int, pct: float) -> None:
            self._enqueue(("usage", used, thresh, pct))

        def blocked_fn(blocked: bool) -> None:
            self._enqueue(("blocked", blocked))
            if blocked:
                self._enqueue(("limit_reached",))

        def work() -> None:
            self._limiter.run(
                self._stop,
                on_log=log_fn,
                on_usage=usage_fn,
                on_blocked=blocked_fn,
                console_progress=False,
            )
            self._enqueue(("log", "Monitoring stopped."))

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.progress.configure(progress_color=ACCENT)
        self._chart_samples.clear()
        self._set_badge(False)
        self._append_log("Monitoring started.")
        self.update_idletasks()
        self.after(0, self._draw_usage_chart)

    def _on_stop(self) -> None:
        if self._stop_busy:
            return
        self._stop_busy = True
        self._append_log("Stop requested — stopping monitoring…")
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="disabled")
        self._stop.set()

        def stop_task() -> None:
            try:
                w = self._worker
                if w is not None and w.is_alive():
                    w.join(timeout=8.0)

                def finish() -> None:
                    try:
                        limit_hit = self._limiter.limit_action_taken
                        self._set_badge(limit_hit)
                        self._chart_samples.clear()
                        self.update_idletasks()
                        self.after(0, self._draw_usage_chart)
                    finally:
                        self.btn_start.configure(state="normal")
                        self.btn_stop.configure(state="disabled")
                        self._stop_busy = False

                self.after(0, finish)
            except Exception as e:
                def recover() -> None:
                    self._append_log(f"Stop error: {e}")
                    self.btn_start.configure(state="normal")
                    self.btn_stop.configure(state="disabled")
                    self._stop_busy = False

                self.after(0, recover)

        threading.Thread(target=stop_task, daemon=True).start()

    def _on_close(self) -> None:
        self._stop.set()
        self.destroy()


def main() -> None:
    if platform.system() not in SUPPORTED_GUI_PLATFORMS:
        print(
            f"{APP_NAME}: GUI supports Windows and macOS only (this OS: {platform.system()}).",
            file=sys.stderr,
        )
        sys.exit(1)
    app = InternetLimiterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
