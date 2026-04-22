#!/usr/bin/env python3
"""
Internet Usage Limiter — CLI
Monitors network usage and blocks internet if threshold exceeded.
"""

import sys
import threading

from limiter_core import (
    NetworkLimiter,
    require_unix_root_cli,
    require_windows_admin_cli,
)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in ("--unblock", "--recover"):
        require_windows_admin_cli()
        require_unix_root_cli()
        limiter = NetworkLimiter()
        limiter.unblock_internet()
        return

    require_windows_admin_cli()
    require_unix_root_cli()

    stop = threading.Event()
    limiter = NetworkLimiter(threshold_mb=20, window_minutes=5)
    limiter.run(stop, console_progress=True)


if __name__ == "__main__":
    main()
