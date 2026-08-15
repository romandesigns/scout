# Scout V5.0.1 notifier hotfix

This release prevents provider rate limits from blocking market ingestion or
causing parallel notification bursts.

## Deploy without replacing secrets or data

From PowerShell 7 on Windows, upload the ZIP to the VPS:

```powershell
scp -P 99 .\stockhunter-scout-v5.0.1-notifier-hotfix.zip wavystack@72.60.30.64:/tmp/
ssh -p 99 wavystack@72.60.30.64
```

Then run these commands on the VPS:

```bash
set -e
cd /opt/apps/scout
cp .env /tmp/scout.env.v5.0.1
cp compose.yaml /tmp/scout.compose.v5.0.1.yaml
tar -czf /tmp/scout-pre-v5.0.1.tgz --exclude=.env --exclude=data --exclude=charts .
unzip -oq /tmp/stockhunter-scout-v5.0.1-notifier-hotfix.zip -d /tmp/scout-v5.0.1
cp -a /tmp/scout-v5.0.1/stockhunter-scout-v5.0/. /opt/apps/scout/
cp /tmp/scout.env.v5.0.1 /opt/apps/scout/.env
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:18081/healthz
```

The existing `.env`, `data/`, and `charts/` are preserved. The pre-hotfix
application source is retained at `/tmp/scout-pre-v5.0.1.tgz` for rollback.

## Verify live delivery

Wait about 30 seconds and run:

```bash
curl -fsS http://127.0.0.1:18081/api/status | python3 -m json.tool
docker logs --since 5m --tail 250 stockhunter-scout
```

In `/api/status`, inspect `notifications.queues` and
`notifications.delivery`. A healthy channel has a recent `last_success_at`, a
null `last_error`, and a null `rate_limited_until`.

If a provider is still limiting requests, Scout will retain findings and retry
at the provider-requested interval. Do not repeatedly restart the container;
that cannot reset a provider-side limit.

## Roll back

```bash
set -e
cd /opt/apps/scout
tar -xzf /tmp/scout-pre-v5.0.1.tgz -C /opt/apps/scout
cp /tmp/scout.env.v5.0.1 /opt/apps/scout/.env
cp /tmp/scout.compose.v5.0.1.yaml /opt/apps/scout/compose.yaml
docker compose up -d --build
```
