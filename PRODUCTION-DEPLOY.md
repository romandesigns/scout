# Scout V5.4 visual-intelligence production deployment

This release replaces the minimal Scout container with the complete same-origin
API and dashboard while preserving the existing `/data` SQLite database,
`/charts`, `.env`, Alpaca configuration, and self-hosted ntfy service.

## Production properties

- Static Next.js dashboard is built into the Python container.
- Dashboard, REST API, charts, and server-sent events share one private origin.
- The Docker port remains bound to `127.0.0.1:18081`.
- Detection and universe range defaults to `$0.15-$10.00` and is persisted when changed from the desktop Settings panel.
- Existing environment values override defaults.
- No order placement or broker trading controls are included.

## Safe in-place upgrade

Run from `/opt/apps/scout` after uploading and extracting this release:

```bash
set -e

cp .env /tmp/scout-v5.1.env
cp compose.yaml /tmp/scout-v5.1-compose.yaml

docker compose config >/tmp/scout-v5.1-compose-check.txt
docker compose build scout
docker compose up -d scout

curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/api/status
```

Do not remove `data`, `charts`, or the independently running `scout-ntfy`
container. The database schema migration is additive and runs automatically.

## Private dashboard URL

Expose the dashboard only to the tailnet on a port separate from ntfy:

```bash
sudo tailscale serve --bg --https=8444 http://127.0.0.1:18081
tailscale serve status
```

Open:

```text
https://srv1170872.tail86523.ts.net:8444
```

Set this same URL in `.env` so ntfy alerts deep-link into the selected finding:

```text
SCOUT_CLIENT_BASE_URL=https://srv1170872.tail86523.ts.net:8444
```

Recreate Scout after changing `.env`.

## Live verification

```bash
curl -fsS http://127.0.0.1:18081/api/status | python3 -m json.tool
curl -fsS 'http://127.0.0.1:18081/api/findings?limit=5' | python3 -m json.tool
curl -N http://127.0.0.1:18081/api/events
```

The dashboard should display `LIVE`, the configured `$0.15-$10.00` range,
current feed health, persisted findings, live chart state, catalysts, delivery
health, and new findings without a page refresh.
