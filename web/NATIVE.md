# Scout V5 native clients

`web/src-tauri` is the shared Tauri 2 shell for the StockHunter Scout Windows and Android clients.

The same Next.js static export is used by Docker and by the native shell. The native client connects to the private Scout API using `NEXT_PUBLIC_SCOUT_API_BASE` at build time.

## Common native behavior

- Scout deep links use `stockhunter-scout://` and `scout://`.
- Finding notifications include the finding id and ticker so opening an alert selects the exact Scout finding.
- Native notification delivery obeys the same platform/signal/session/minimum-score/quiet-hours preferences as the workstation.
- Scout stores per-platform alert priority. Android maps it to native notification-channel importance; server-side ntfy maps it to ntfy priority while critical Scout signals remain maximum priority.
- The browser build degrades gracefully when native Tauri APIs are not present.

## Windows

Implemented in the Tauri shell:

- single application instance;
- native Scout notifications from live finding SSE events;
- deep-link handling;
- system tray icon;
- Open Scout / Quit Scout tray menu;
- left-click tray restore/focus;
- closing the main window hides it to the tray rather than terminating Scout;
- optional launch-at-sign-in controlled from Scout Settings;
- window state persistence.

The close-to-tray behavior intentionally keeps the WebView/SSE client alive so Windows alerts can continue while the workstation window is hidden.

## Android

Implemented in the client:

- native notification permission flow;
- `scout-critical` and `scout-default` Android channels;
- vibration preference synchronization;
- configurable Android alert priority (`low`, `normal`, `high`, `critical`) mapped to native channel importance;
- interactive finding action (`View Scout`);
- deep-link navigation into the selected finding.

Android OS/ntfy channel settings can still override sound presentation, so Scout treats the sound switch as a delivery preference rather than claiming to bypass device-level notification policy. The production background transport remains **ntfy**. This is intentional: it allows the server to wake/deliver to Android when the Tauri WebView is not running. The native client owns the Scout UI and local notification experience when connected; ntfy remains the reliable server-to-phone background transport.

## Local Windows development

```powershell
cd web
bun install
bun tauri dev
```

For a live VPS API, create `.env.local` before starting the native dev build:

```env
NEXT_PUBLIC_SCOUT_API_BASE=http://127.0.0.1:18081
```

When the VPS port is bound to loopback, create the SSH/Tailscale forward on Windows first.

## Windows release build

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./build-windows.ps1
```

The release script lives in the project root, validates prerequisites, injects
the private production Scout API URL, runs TypeScript validation, builds the
NSIS installer, and copies it to `release/windows`. See `WINDOWS-BUILD.md`.

## Android development

Install the Tauri/Rust/Android SDK prerequisites, then:

```powershell
cd web
bun install
bun tauri android init
bun tauri android dev
```

Release packaging can then use the corresponding Tauri Android build command for your configured signing environment.

## Testing checklist

- Windows: app opens, only one instance, tray restore works, close hides to tray, launch-at-sign-in toggle works, test toast appears, clicking/deep-linking selects the correct finding.
- Android: notification permission granted, default/critical channels exist, vibration preference syncs, test notification appears, finding action/deep link opens the correct finding.
- Both: per-signal notification mode, session gate, minimum score and quiet hours match server preferences.
