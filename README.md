# Internet Limiter

Desktop tool that watches **total network usage** (upload + download) over a **rolling time window** and, when a **threshold** is exceeded, **blocks internet access** using the OS firewall or packet filter. Useful for self-imposed limits; use only if you understand the recovery steps below.

**Platforms:** graphical app on **Windows** and **macOS**. Shared core also supports **Linux** via the CLI (`iptables`), which is best treated as advanced.

## Important warnings

- **Administrator (Windows) or root (macOS/Linux)** is required to apply and remove blocks.
- When the limit is hit, the machine may have **no working internet** until you **stop monitoring and restore** or run recovery (see below).
- If the app **crashes or is force-closed** while blocking, you may need to **restore** using the GUI prompt on next launch or the CLI recovery command.
- On Windows, recovery normally **imports a saved firewall export** (`.wfw`). If that fails, follow the on-screen log; you may need to run recovery **as Administrator** again.

## Requirements

- **Python 3.10+**
- Dependencies: see [`requirements.txt`](requirements.txt)

## Run from source (GUI)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app_gui.py
```

When you start monitoring, the app will need elevated rights (UAC on Windows, or password via `osascript` / `sudo` on macOS as documented in the app).

## CLI (Windows / macOS / Linux)

```bash
# After the same venv + pip install
python internet_limiter.py
```

Recovery without the full monitor loop:

```bash
# Windows: elevated Command Prompt or PowerShell
python internet_limiter.py --unblock

# macOS / Linux
sudo python internet_limiter.py --unblock
```

## Building a standalone executable

- **Windows:** run [`build_windows.bat`](build_windows.bat) (requires PyInstaller; see [`requirements-build.txt`](requirements-build.txt)). Output: `dist/InternetLimiter.exe` (requests admin via UAC).
- **macOS:** run [`build_macos.sh`](build_macos.sh). Output: `dist/InternetLimiter.app`.

Optional **Inno Setup** installer script: [`installer.iss`](installer.iss) (expects the built `.exe` in `dist/`).

**Note:** Packed executables sometimes trigger **antivirus false positives**. Prefer signed builds for distribution; compare checksums when downloading releases.

## Repository layout

| File | Role |
|------|------|
| [`app_gui.py`](app_gui.py) | CustomTkinter GUI (Windows / macOS) |
| [`limiter_core.py`](limiter_core.py) | Monitoring, firewall / pf / iptables logic |
| [`internet_limiter.py`](internet_limiter.py) | CLI entry point |
| [`net_control.bat`](net_control.bat) | Optional Windows helper for CLI workflow (review before use; stopping uses `taskkill` on `python.exe`) |

## License

[MIT](LICENSE)

## Security

See [SECURITY.md](SECURITY.md).
