#!/usr/bin/env python3
"""
Internet Usage Limiter — CLI
Monitors network usage and closes v2rayN when the threshold is exceeded.
"""

import sys
import threading

from limiter_core import NetworkLimiter, reset_windows_system_proxy


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("--reset-proxy",):
        reset_windows_system_proxy()
        return

    stop = threading.Event()
    limiter = NetworkLimiter(threshold_mb=20, window_minutes=5)
    limiter.run(stop, console_progress=True)


if __name__ == "__main__":
    main()
