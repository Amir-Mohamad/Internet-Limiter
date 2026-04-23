#!/usr/bin/env python3
"""Core network limiting logic (CLI and GUI share this)."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import psutil

FW_BACKUP_FILENAME = "pre_block_firewall.wfw"
SESSION_FILENAME = "blocking_session.json"
PF_BACKUP_NAME = "pf_backup.conf"
PF_BLOCK_NAME = "pf_block.rules"

_DARWIN_PF_RULES_BLOCK = """# Internet Limiter — temporary block (restored on Stop)
set skip on lo0
block all
"""


def _app_state_dir() -> Path:
    sysname = platform.system()
    if sysname == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            raise RuntimeError("LOCALAPPDATA is not set")
        return Path(local) / "InternetLimiter"
    if sysname == "Darwin":
        return Path.home() / "Library" / "Application Support" / "InternetLimiter"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "internet-limiter"
    return Path.home() / ".local" / "state" / "internet-limiter"


def _ensure_app_state_dir() -> Path:
    d = _app_state_dir()
    d.mkdir(parents=True, exist_ok=True)
    if platform.system() != "Windows":
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    return d


def _windows_fw_backup_path() -> Path:
    return _app_state_dir() / FW_BACKUP_FILENAME


def _darwin_pf_backup_path() -> Path:
    return _app_state_dir() / PF_BACKUP_NAME


def _darwin_pf_block_path() -> Path:
    return _app_state_dir() / PF_BLOCK_NAME


def _write_blocking_session() -> None:
    d = _ensure_app_state_dir()
    payload = {
        "app": "InternetLimiter",
        "blocked_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "os": platform.system(),
    }
    (d / SESSION_FILENAME).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def _clear_blocking_session() -> None:
    try:
        p = _app_state_dir() / SESSION_FILENAME
    except RuntimeError:
        return
    p.unlink(missing_ok=True)


def has_stale_blocking_state() -> bool:
    """True if a previous run may have left firewall/pf blocking (crash, force-kill)."""
    try:
        d = _app_state_dir()
    except RuntimeError:
        return False
    if not d.is_dir():
        return False
    if (d / SESSION_FILENAME).is_file():
        return True
    if platform.system() == "Windows" and (d / FW_BACKUP_FILENAME).is_file():
        return True
    return False


def _subprocess_run(args, **kwargs):
    """Run subprocess without a console window on Windows (avoids flash with windowed .exe)."""
    if platform.system() == "Windows":
        flags = kwargs.pop("creationflags", 0)
        kwargs["creationflags"] = int(flags) | subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    return subprocess.run(args, **kwargs)


def _windows_export_firewall(log: LogFn, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = _subprocess_run(
        ["netsh", "advfirewall", "export", str(dest.resolve())],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        log(f"Could not export firewall state before block: {err or r.returncode}")
        return False
    return True


def _windows_import_firewall(log: LogFn, src: Path) -> bool:
    if not src.is_file():
        return False
    r = _subprocess_run(
        ["netsh", "advfirewall", "import", str(src.resolve())],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        log(f"Firewall import failed: {err or r.returncode}")
        return False
    return True


def _windows_end_retry_prone_apps(log: LogFn) -> None:
    """After a block, force-close apps that tend to busy-loop on failed connections (Windows)."""
    # /T = process tree (v2rayN spawns v2ray/xray). 128 = no matching process.
    targets: list[tuple[str, str]] = [
        ("v2rayN.exe", "v2rayN"),
        ("Telegram.exe", "Telegram Desktop"),
    ]
    for image, label in targets:
        r = _subprocess_run(
            ["taskkill", "/F", "/T", "/IM", image],
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            log(f"Ended task: {label}")
        elif r.returncode == 128:
            pass
        else:
            err = (r.stderr or r.stdout or "").strip()
            log(f"Could not end {label}: {err or r.returncode}")


LogFn = Callable[[str], None]
UsageFn = Callable[[int, int, float], None]  # window_bytes, threshold_bytes, percent
BlockedFn = Callable[[bool], None]


def _darwin_pf_backup_current(log: LogFn) -> None:
    backup = _darwin_pf_backup_path()
    _ensure_app_state_dir()
    r = _subprocess_run(
        ["/sbin/pfctl", "-sr"],
        capture_output=True,
        text=True,
    )
    text = (r.stdout or "").strip()
    if r.returncode != 0:
        err = (r.stderr or "").strip()
        if err:
            log(f"pfctl -sr: {err}")
        backup.unlink(missing_ok=True)
        return
    if not text:
        backup.unlink(missing_ok=True)
        log("No active pf rules to back up (packet filter may be off).")
        return
    try:
        backup.write_text(r.stdout, encoding="utf-8")
        log("Backed up active pf rules for restore.")
    except OSError as e:
        log(f"Could not save pf backup: {e}")


def _darwin_apply_block(log: LogFn) -> None:
    _ensure_app_state_dir()
    _darwin_pf_backup_current(log)
    block_path = _darwin_pf_block_path()
    try:
        block_path.write_text(_DARWIN_PF_RULES_BLOCK, encoding="utf-8")
    except OSError as e:
        raise RuntimeError(f"Could not write pf block rules: {e}") from e
    _subprocess_run(["/sbin/pfctl", "-f", str(block_path)], check=True)
    en = _subprocess_run(["/sbin/pfctl", "-e"], capture_output=True, text=True)
    if en.returncode != 0:
        msg = (en.stderr or en.stdout or "").strip()
        if msg and "enabled" not in msg.lower():
            log(f"pfctl -e: {msg}")


def _darwin_restore_pf(log: LogFn) -> bool:
    backup = _darwin_pf_backup_path()
    if not backup.is_file():
        off = _subprocess_run(["/sbin/pfctl", "-d"], capture_output=True, text=True)
        if off.returncode != 0:
            m = (off.stderr or off.stdout or "").strip()
            if m:
                log(f"pfctl -d: {m}")
            return False
        log("Packet filter disabled (no rule backup was saved).")
        return True

    try:
        body = backup.read_text(encoding="utf-8").strip()
    except OSError as e:
        log(f"Could not read pf backup: {e}")
        body = ""

    if body:
        try:
            _subprocess_run(["/sbin/pfctl", "-f", str(backup)], check=True)
            log("Restored previous pf rules.")
            backup.unlink(missing_ok=True)
            return True
        except Exception as e:
            log(f"Error restoring pf rules: {e}")
            return False

    backup.unlink(missing_ok=True)
    _subprocess_run(["/sbin/pfctl", "-d"], check=False)
    log("Packet filter disabled.")
    return True


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
        self.is_blocked = False
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

    def block_internet(self, on_log: Optional[LogFn] = None) -> None:
        log = on_log or print
        if self.is_blocked:
            return

        log("Blocking internet — threshold exceeded.")

        try:
            if self.os_type == "Linux":
                _subprocess_run(
                    ["iptables", "-I", "OUTPUT", "1", "-j", "DROP"], check=True
                )
                _subprocess_run(
                    ["iptables", "-I", "INPUT", "1", "-j", "DROP"], check=True
                )
                _write_blocking_session()
            elif self.os_type == "Darwin":
                _darwin_apply_block(log)
                _write_blocking_session()
            elif self.os_type == "Windows":
                _ensure_app_state_dir()
                fw_backup = _windows_fw_backup_path()
                if not _windows_export_firewall(log, fw_backup):
                    return
                _subprocess_run(
                    ["netsh", "advfirewall", "set", "allprofiles", "state", "on"],
                    check=True,
                )
                _subprocess_run(
                    [
                        "netsh",
                        "advfirewall",
                        "set",
                        "allprofiles",
                        "firewallpolicy",
                        "blockinbound,blockoutbound",
                    ],
                    check=True,
                )
                _write_blocking_session()
                _windows_end_retry_prone_apps(log)
            else:
                log(f"Blocking not implemented for OS: {self.os_type}")
                return

            self.is_blocked = True
            log("Internet blocked.")
        except Exception as e:
            log(f"Error blocking internet: {e}")

    def unblock_internet(self, on_log: Optional[LogFn] = None) -> None:
        log = on_log or print
        log("Unblocking internet…")

        try:
            if self.os_type == "Linux":
                _subprocess_run(
                    ["iptables", "-D", "OUTPUT", "-j", "DROP"], check=False
                )
                _subprocess_run(["iptables", "-D", "INPUT", "-j", "DROP"], check=False)
                _clear_blocking_session()
            elif self.os_type == "Darwin":
                ok = _darwin_restore_pf(log)
                if ok:
                    _clear_blocking_session()
                else:
                    log("pf restore reported errors; session marker kept for retry.")
                    return
            elif self.os_type == "Windows":
                backup = _windows_fw_backup_path()
                if backup.is_file():
                    if _windows_import_firewall(log, backup):
                        backup.unlink(missing_ok=True)
                        _clear_blocking_session()
                    else:
                        log("Firewall import failed; backup file kept. Retry as Administrator.")
                        return
                else:
                    log(
                        "No firewall backup found; resetting policy to "
                        "BlockInbound,AllowOutbound (may differ from your previous settings)."
                    )
                    _subprocess_run(
                        [
                            "netsh",
                            "advfirewall",
                            "set",
                            "allprofiles",
                            "firewallpolicy",
                            "blockinbound,allowoutbound",
                        ],
                        check=True,
                    )
                    _clear_blocking_session()
            else:
                log(f"Unblocking not implemented for OS: {self.os_type}")
                return

            self.is_blocked = False
            log("Internet unblocked.")
        except Exception as e:
            log(f"Error unblocking: {e}")

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

                if window_usage > self.threshold_bytes and not self.is_blocked:
                    self.block_internet(on_log=log)
                    if on_blocked and self.is_blocked:
                        on_blocked(True)

                # wait() returns as soon as stop is set — avoids freezing Stop on full sleep
                stop_event.wait(self.check_interval)
        except KeyboardInterrupt:
            log("\nStopping monitor…")
            if self.is_blocked:
                log("Restoring network after interrupt…")
                self.unblock_internet(on_log=log)
            elif has_stale_blocking_state():
                log("Attempting to clear leftover firewall/pf state from this tool…")
                self.unblock_internet(on_log=log)
        finally:
            if console_progress:
                print()


def is_windows_admin() -> bool:
    if platform.system() != "Windows":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def is_superuser() -> bool:
    """Windows: elevated admin. macOS/Linux: effective UID 0 (e.g. sudo)."""
    if platform.system() == "Windows":
        return is_windows_admin()
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return False
    return geteuid() == 0


def require_windows_admin_cli() -> None:
    if platform.system() != "Windows":
        return
    if not is_windows_admin():
        print("Please run as Administrator.", file=sys.stderr)
        sys.exit(1)


def require_unix_root_cli() -> None:
    if platform.system() == "Windows":
        return
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and geteuid() != 0:
        print("Please run with sudo.", file=sys.stderr)
        sys.exit(1)
