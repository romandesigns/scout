# Windows production installer

The Windows client is built on Windows 11 and connects directly to the private
Scout API through Tailscale.

## One-time prerequisites

Run in PowerShell 7:

```powershell
winget install --id Oven-sh.Bun -e
winget install --id Rustlang.Rustup -e
winget install --id Microsoft.VisualStudio.2022.BuildTools -e `
  --override "--wait --passive --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

Restart PowerShell after installation. WebView2 is already included with
current Windows 11 installations; update Microsoft Edge/WebView2 if Tauri says
the runtime is missing.

## Build

Extract the release ZIP, open PowerShell 7 in its root, and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./build-windows.ps1 -SkipApiCheck
```

Before running without `-SkipApiCheck`, deploy the VPS dashboard and connect
Tailscale on Windows:

```powershell
./build-windows.ps1
```

The installer is written to:

```text
release\windows\StockHunter Scout_6.0.0_x64-setup.exe
```

The installer contains the static workstation and is permanently configured to
use:

```text
https://srv1170872.tail86523.ts.net:8444
```

Tailscale must be connected when Scout is used. The API and dashboard remain
private and are not opened to the public internet.

## Release verification

1. Install the generated `.exe`.
2. Open Scout and confirm the header says `LIVE`.
3. Confirm the `$0.15-$10.00` range and current universe count; adjust it in Settings if needed.
4. Open a finding and confirm its 15-second chart loads.
5. Close the window and reopen it from the tray.
6. Enable launch at sign-in from Settings and verify the saved state.
7. Send a Windows test notification from Notification Settings.
8. Click a finding notification and confirm Scout focuses the matching ticker.
