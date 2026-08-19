# Scout V6.5.0 — Rust-Primary Hybrid Production Integration

Version 6.5.0 wires the frozen v6.4.13 Rust primary perception engine into Scout's live runtime while retaining Python specialist/context intelligence, lifecycle handling, and unique candidate coverage. See [V6.5-IMPLEMENTATION.md](V6.5-IMPLEMENTATION.md).

V5.9 keeps the catalyst engine listening continuously and delivers background Web Push notifications to the installed Android/iPhone PWA. News begins as `CATALYST_WATCH` and becomes `CATALYST_ACTIVE` only when clean market-reaction evidence confirms participation. VAPID keys are generated automatically during the coordinated VPS deployment. As of 2026-08-19, native delivery is primary per client (Tauri desktop toast, installed mobile push, in-page toast for a plain browser tab) with self-hosted ntfy as the background/backup channel everywhere.

Private, self-hosted **bullish market monitoring and decision-support** platform for detecting low-priced U.S. stocks before or near momentum ignition. Scout does **not** place, route, prepare, or automate trades.

V5.4 combines early bullish detection with a market-quality gate, ticker episode lifecycle, actionable ranking, validation, notifications, API, responsive workstation, and native Windows/Android shell. The live tracking and alert universe is `$0.15-$10.00` by default and can be changed live from Settings without editing `.env`.

The inspector now presents candidate quality as radar, radial, velocity, and participation charts. Gap, prior close, session volume, projected session volume, and volume velocity are derived from the live Alpaca snapshot and tape. Validation includes average follow-through and a time-of-day view.

## One-command coordinated release

From PowerShell 7 on the Windows workstation:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\release-all.ps1
```

This builds and installs the NSIS desktop client, uploads the matching backend/PWA through the VPS Tailscale hostname, creates a server backup, preserves `.env`/data/charts, rebuilds Docker, and verifies the backend. Upload/deploy retries are automatic. Desktop installation still runs if the remote stage fails, and the final table reports Build, Desktop, VPS, and PWA separately. Re-run with `-SkipBuild` to resume deployment from an existing installer, or use the other skip switches for a partial release.

## Product priority

Scout is built around one question: **what is beginning to move, why does it matter, and did Scout see it early enough?**

The priority hierarchy is:

1. earliest credible signal;
2. immediate evidence;
3. chart context and exact Scout markers;
4. catalyst correlation;
5. current status / Top Gainers / halts;
6. validation of what happened after detection;
7. fast Windows and Android notification delivery.

## V5 detector engines

The existing V4 quality gates are preserved and extended with event fusion.

- `EARLY` — abnormal participation + bullish wake-up evidence.
- `SURGE` — event-driven 3s / 5s / 10s / 15s acceleration with participation gates.
- `BREAKOUT` — 1m / 3m / 5m completed-bucket resistance break with price penetration + participation + structure.
- `STAIRCASE` — gradual multi-bucket higher-low / rising-participation wake-up.
- `IGNITION` — stronger participation, velocity and structure confirmation.
- `HALT_PRESSURE` — urgent regular-session acceleration with clean structure, liquidity, trade participation, and relative-volume evidence; it does not guarantee a halt.
- `REARM` — later continuation / re-breakout after an already-qualified momentum episode.
- `REVERSAL_WATCH` — silent tracking after a material intraday selloff forms a local low.
- `EMA_RECLAIM` / `VWAP_RECLAIM` — actionable fresh episodes labeled by the structure actually recovered; both require fresh participation.
- `FIRST_PULLBACK` / `REARM` — controlled post-reclaim pullback and renewed demand confirmation.
- `CATALYST` — bullish catalyst finding with live market context.
- `HALT` / `RESUME` — market-status events with Scout history retained.

One finding can carry multiple fused signals, for example:

```text
EARLY · SURGE · BREAKOUT · IGNITION · CATALYST
```

The fastest path does not wait for a completed 60-second candle. The rolling 3/5/10/15-second velocity windows are updated from incoming trades; 60 seconds remains context.

## Workstation

The web client is a responsive Next.js workstation inspired by VS Code workbench behavior and dense financial-market presentation.

Desktop:

- icon-only Activity Rail;
- Primary Sidebar for Radar, Catalysts, Top Gainers, Halted, Validation, Alerts and Settings;
- editor-group chart workspace with single, split, and four-pane layouts;
- independently resizable Secondary Inspector;
- collapsible/resizable/maximizable bottom intelligence dock;
- command palette (`Ctrl/Cmd+K`);
- notification center and full notification preferences.

The major desktop surfaces deliberately use **gutter and surface contrast instead of heavy borders**. Major rounded panels are separated by a 6px exposed canvas gutter; chart groups use a 4px gutter. Borders are reserved for states where an outline actually communicates something.
Navigation, pane headers, chart groups, settings controls, and market rows therefore default to borderless surfaces with only low-contrast row separators where scanning benefits from them. Selection and signal state are communicated with fill, accent bars, typography, and badges instead of boxes around every element.

Mobile:

- purpose-built layout rather than a squeezed desktop workbench;
- icon-only Radar / Gainers / Halted controls;
- icon-only bottom navigation;
- full-screen selected chart + evidence;
- Catalysts, Alerts and Settings remain one tap away;
- accessible labels remain available even when navigation text is visually hidden.

## Live market views

### Radar

Ranks current Scout findings and shows fused signal badges plus fast evidence:

- 3s / 5s / 10s / 15s / 30s price velocity;
- acceleration;
- 15s / 30s relative volume;
- dollar volume and trade count;
- EMA9 / EMA21;
- VWAP;
- quiet-range escape;
- breakout level / window;
- catalyst presence.

### Top Gainers

Uses Alpaca's real-time movers data and overlays the most recent Scout finding for each symbol. This exposes the key comparison: **where was Scout when the ticker later became an obvious top gainer?**

### Halted

Tracks live market-status messages, persists halt/resume events, and preserves the pre-halt Scout timeline.

### Catalysts

Alpaca News, SEC monitoring, and optional RSS/Atom feeds are correlated with live findings. The timeline can show sequences such as:

```text
CATALYST → EARLY → SURGE → BREAKOUT → IGNITION → HALT / RESUME
```

## Charts

The live chart polls Scout's warm state cache and renders 15-second candlesticks with:

- volume;
- EMA9;
- EMA21;
- VWAP;
- current price;
- catalyst markers;
- fused Scout finding markers;
- halt/resume markers.
- selected-event formation, detection, trigger, and invalidation annotations.

A chart group can switch between the live chart and the **frozen detection chart** produced at alert time. Frozen charts never include future candles.

Chart annotations can be disabled globally or controlled individually from the Chart section in Settings. The preference is device-local.

## Validation

Scout automatically tracks post-detection maximum move over:

- +1 minute;
- +5 minutes;
- +15 minutes;
- the remainder of the trading session.

The Validation view is designed to answer whether a detector is early, useful, late, or missing the move.

**Important:** the current API field named `move_at_detection_pct` is backed by Scout's stored quiet-base `extension_pct` for that finding. It is not guaranteed to represent change from the prior regular-session close. Future replay tooling can add a separate explicit first-print/prior-close reference without changing this field silently.

## Notification system

Notification preferences include per-platform enablement, per-signal notify/silent/off modes, sessions, quiet hours, minimum score, grouping/escalation behavior, Windows toast control, Android vibration, and per-platform priority. Android priority is mapped into both the native channel importance and server-side ntfy priority; Scout critical signals (`SURGE`, `IGNITION`, `HALT`) remain maximum priority. Device/OS notification policy can still override audible presentation.

Preferences are persisted in SQLite and enforced by the server-side notification paths.

Controls include:

- master enable / disable;
- Android, Windows and email independently;
- per-signal `notify`, `silent`, or `off`;
- Overnight / Premarket / Regular / After-hours sessions;
- quiet hours;
- critical-signal bypass;
- minimum score;
- stage-escalation suppression;
- grouping by ticker;
- sound / vibration / toast behavior where supported;
- test notifications.

### Android

The Android installed client supports native Scout notifications while connected, Android notification channels, finding actions, and deep links. **ntfy remains the server-side background/wake transport** so alerts can still reach the phone when the Scout WebView is not alive.

### Windows

The Windows installed client supports native notifications, deep links, single-instance behavior, system tray behavior, close-to-tray, and optional launch-at-sign-in. Keeping the window in the tray preserves the live SSE client instead of terminating it.

## API

```text
GET  /healthz
GET  /api/status
GET  /api/findings
GET  /api/findings/{id}
GET  /api/findings/{id}/verification
PUT  /api/findings/{id}/review
GET  /api/catalysts
GET  /api/market/gainers
GET  /api/market/halts
GET  /api/market/snapshot/{ticker}
GET  /api/market/diagnostics/{ticker}
GET  /api/validation
GET  /api/timeline
GET  /api/notifications/preferences
PUT  /api/notifications/preferences
POST /api/notifications/test
GET  /api/events                    # server-sent events
GET  /charts/{filename}
```

The Docker image serves the static workstation at `/` on the same private Scout port, so production uses same-origin API requests.

## Data flow

```text
Alpaca SIP / BOATS / News + SEC / optional RSS
                  │
                  ▼
          Scout detector + catalyst engine
                  │
        ┌─────────┼───────────┐
        ▼         ▼           ▼
     SQLite     Charts      SSE events
        │         │           │
        └─────────┴──────┬────┘
                         ▼
                 Scout Workstation
                  │              │
                  ▼              ▼
             Windows app     Android app
                  │              │
              native toast   native/ntfy push
```

## Sessions

```text
8:00 PM ─ 4:00 AM   BOATS overnight
4:00 AM ─ 9:30 AM   SIP premarket
9:30 AM ─ 4:00 PM   SIP regular
4:00 PM ─ 8:00 PM   SIP after-hours
```

## Local web development

### Demo mode

If no API base is configured, the client renders deterministic demo data and does not poll nonexistent local `/api/*` routes.

```powershell
cd web
bun install
bun run dev
```

Open `http://localhost:3000`.

### Live local workstation against the VPS

Keep the production detector on the VPS and forward its private port to Windows. Then create `web/.env.local`:

```env
NEXT_PUBLIC_SCOUT_API_BASE=http://127.0.0.1:18081
```

The server must allow the browser origin, typically `http://localhost:3000`, through `SCOUT_ALLOWED_ORIGINS`.

## Docker / VPS deployment

Copy `.env.example` to `.env`, preserve your existing production secrets, and build:

```bash
docker compose up -d --build
```

Health:

```bash
curl -fsS http://127.0.0.1:18081/healthz
```

The existing compose binding remains private:

```text
127.0.0.1:18081 -> 8080
```

Use Tailscale / a private reverse proxy / SSH forwarding instead of opening the Scout port publicly.

## Native development

From `web/`:

```powershell
bun install
bun tauri dev
```

Android after the Tauri Android/Rust prerequisites are installed:

```powershell
bun tauri android init
bun tauri android dev
```

See `web/NATIVE.md` for native-specific behavior and build notes.

## Repository layout

```text
app/
  api.py
  catalysts.py
  charts.py
  classifier.py
  config.py
  db.py
  dispatch.py
  events.py
  indicators.py
  main.py
  market.py
  models.py
  notifiers.py
  preferences.py
web/
  app/
  components/
  lib/
  src-tauri/
  package.json
scripts/
Dockerfile
compose.yaml
requirements.txt
.env.example
```

## Safety boundary

Scout is strictly monitoring and decision support. There is no order-placement implementation and no UI control for buying, selling, routing, preparing, or automating trades.
