#!/usr/bin/env python3
"""Core network limiting logic (CLI and GUI share this)."""

from __future__ import annotations

import platform
import subprocess
import threading
import time
from collections import deque
from typing import Callable, Optional

import psutil

LogFn = Callable[[str], None]
UsageFn = Callable[[int, int, float], None]  # window_bytes, threshold_bytes, percent
BlockedFn = Callable[[bool], None]


def _subprocess_run(args, **kwargs):
    """Run subprocess without a console window on Windows (avoids flash with windowed .exe)."""
    if platform.system() == "Windows":
        flags = kwargs.pop("creationflags", 0)
        kwargs["creationflags"] = int(flags) | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return subprocess.run(args, **kwargs)


def _windows_disable_system_proxy(log: LogFn) -> None:
    """Turn off WinINet user proxy. v2rayN normally does this on exit; forced kill may skip that."""
    if platform.system() != "Windows":
        return
    try:
        import ctypes
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 0)
        finally:
            winreg.CloseKey(key)

        INTERNET_OPTION_SETTINGS_CHANGED = 39
        INTERNET_OPTION_REFRESH = 37
        try:
            inet = ctypes.windll.wininet
            inet.InternetSetOptionW(0, INTERNET_OPTION_SETTINGS_CHANGED, 0, 0)
            inet.InternetSetOptionW(0, INTERNET_OPTION_REFRESH, 0, 0)
        except Exception:
            pass
        log("System proxy disabled (WinINet).")
    except OSError as e:
        log(f"Could not disable system proxy: {e}")


def reset_windows_system_proxy(on_log: Optional[LogFn] = None) -> None:
    """CLI helper: disable WinINet system proxy (no admin required). No-op on other OS."""
    log = on_log or print
    if platform.system() != "Windows":
        log("reset-proxy applies to Windows only; nothing to do.")
        return
    _windows_disable_system_proxy(log)


def _close_v2rayn(log: LogFn) -> None:
    """Terminate v2rayN (including child processes on Windows). Then ensure proxy is off on Windows."""
    sysname = platform.system()

    if sysname == "Windows":
        r = _subprocess_run(
            ["taskkill", "/F", "/T", "/IM", "v2rayN.exe"],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            log("Closed v2rayN.")
        elif r.returncode == 128:
            log("v2rayN does not appear to be running.")
        else:
            err = (r.stderr or r.stdout or "").strip()
            log(f"Could not close v2rayN: {err or r.returncode}")
        _windows_disable_system_proxy(log)
        return

    killed_any = False
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if name != "v2rayn" and name != "v2rayn.exe":
            continue
        try:
            pid = proc.pid
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            proc.kill()
            killed_any = True
            log(f"Closed v2rayN (PID {pid}).")
        except psutil.Error as e:
            log(f"Could not close v2rayN: {e}")

    if not killed_any:
        log("v2rayN does not appear to be running.")


class NetworkLimiter:
    def __init__(
        self,
        threshold_mb: float = 20,
        window_minutes: float = 5,
        check_interval: float = 2,
    ):
        self.threshold_bytes = int(threshold_mb * 1024 * 1024)
        self.window_seconds = int(window_minutes * 60)
        self.check_interval = check_interval
        self.usage_history: deque = deque()
        self.limit_action_taken = False
        self.os_type = platform.system()

    def get_network_usage(self) -> int:
        net_io = psutil.net_io_counters()
        return net_io.bytes_sent + net_io.bytes_recv

    def calculate_window_usage(self) -> int:
        now = time.time()
        cutoff_time = now - self.window_seconds

        while self.usage_history and self.usage_history[0][0] < cutoff_time:
            self.usage_history.popleft()

        if len(self.usage_history) < 2:
            return 0

        total_usage = self.usage_history[-1][1] - self.usage_history[0][1]
        return max(0, total_usage)

    def close_v2rayn_on_limit(self, on_log: Optional[LogFn] = None) -> None:
        log = on_log or print
        if self.limit_action_taken:
            return

        log("Usage limit exceeded — closing v2rayN immediately.")
        _close_v2rayn(log)
        self.limit_action_taken = True

    @staticmethod
    def format_bytes(bytes_val: float) -> str:
        for unit in ["B", "KB", "MB", "GB"]:
            if bytes_val < 1024:
                return f"{bytes_val:.2f} {unit}"
            bytes_val /= 1024
        return f"{bytes_val:.2f} TB"

    def _console_progress(self, current_usage: int) -> None:
        percentage = min(100.0, (current_usage / self.threshold_bytes) * 100)
        bar_length = 40
        filled = int(bar_length * percentage / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        print(
            f"\r[{bar}] {percentage:.1f}% | "
            f"{self.format_bytes(current_usage)}/{self.format_bytes(self.threshold_bytes)}",
            end="",
            flush=True,
        )

    def run(
        self,
        stop_event: threading.Event,
        on_log: Optional[LogFn] = None,
        on_usage: Optional[UsageFn] = None,
        on_blocked: Optional[BlockedFn] = None,
        console_progress: bool = False,
    ) -> None:
        log = on_log or print

        if console_progress:
            log("Monitoring network usage…")
            log(
                f"Threshold: {self.format_bytes(self.threshold_bytes)} "
                f"in {self.window_seconds // 60} minutes"
            )
            log(f"OS: {self.os_type}")
            log("On limit: v2rayN will be closed (internet is not firewall-blocked).")
        else:
            log(
                f"Monitoring — {self.format_bytes(self.threshold_bytes)} per "
                f"{self.window_seconds // 60} min window · {self.os_type}"
            )

        try:
            while not stop_event.is_set():
                current_bytes = self.get_network_usage()
                self.usage_history.append((time.time(), current_bytes))

                window_usage = self.calculate_window_usage()
                pct = (
                    min(100.0, (window_usage / self.threshold_bytes) * 100)
                    if self.threshold_bytes
                    else 0.0
                )

                if console_progress:
                    self._console_progress(window_usage)
                elif on_usage:
                    on_usage(window_usage, self.threshold_bytes, pct)

                if window_usage > self.threshold_bytes and not self.limit_action_taken:
                    self.close_v2rayn_on_limit(on_log=log)
                    if on_blocked and self.limit_action_taken:
                        on_blocked(True)

                stop_event.wait(self.check_interval)
        except KeyboardInterrupt:
            log("\nStopping monitor…")
        finally:
            if console_progress:
                print()
