# Security

## Scope

Internet Limiter intentionally modifies **firewall and packet-filter** settings using **elevated privileges** (Windows Administrator, macOS/Linux root). That is required for its behavior and is also its main risk surface: a compromised or tampered binary could abuse those privileges.

This project does not implement remote control, telemetry, or network endpoints in the application code.

## Reporting issues

If you discover a security vulnerability (for example, unexpected remote code execution or unsafe subprocess usage), please open a **private** discussion with the maintainer or report via GitHub **Security Advisories** if enabled for the repository, rather than filing a public issue with exploit details.

## Supply chain

- Prefer running from **source** and installing dependencies from `requirements.txt` in a virtual environment if you want maximum transparency.
- Official **release binaries** should be distributed with **checksums** and ideally **code signing** (Windows Authenticode, Apple notarization) to reduce tampering and antivirus false positives.

## Local data

The app stores firewall/pf backup files and a small session marker under the OS application data directory (for example `%LOCALAPPDATA%\InternetLimiter` on Windows). These files reflect local firewall or pf configuration state, not passwords.
