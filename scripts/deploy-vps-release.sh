#!/usr/bin/env bash
set -euo pipefail

archive="${1:?release archive is required}"
version="${2:?release version is required}"
app_dir="${3:-/opt/apps/scout}"
use_cache="${4:-0}"

test -f "$archive"
test -d "$app_dir"
test -w "$app_dir" || { echo "$app_dir is not writable by $(id -un)" >&2; exit 1; }

backup_dir="$HOME/scout-backups"
mkdir -p "$backup_dir"
backup="$backup_dir/scout-before-$version-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$backup" --exclude='scout/data' --exclude='scout/charts' -C "$(dirname "$app_dir")" "$(basename "$app_dir")"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
unzip -q "$archive" -d "$stage"
release_root="$(dirname "$(find "$stage" -name compose.yaml -print -quit)")"
test -f "$release_root/VERSION"
actual_version="$(tr -d '\r\n' < "$release_root/VERSION")"
test "$actual_version" = "$version" || { echo "Archive version $actual_version does not match $version" >&2; exit 1; }

rsync -a --exclude='.env' --exclude='data/' --exclude='charts/' "$release_root/" "$app_dir/"

env_file="$app_dir/.env"
test -f "$env_file"
if grep -q '^APP_VERSION=' "$env_file"; then
  sed -i "s/^APP_VERSION=.*/APP_VERSION=$version/" "$env_file"
else
  printf '\nAPP_VERSION=%s\n' "$version" >> "$env_file"
fi
if grep -q '^NTFY_MIN_INTERVAL_SECONDS=' "$env_file"; then
  sed -i 's/^NTFY_MIN_INTERVAL_SECONDS=.*/NTFY_MIN_INTERVAL_SECONDS=8/' "$env_file"
else
  printf '\nNTFY_MIN_INTERVAL_SECONDS=8\n' >> "$env_file"
fi

cd "$app_dir"
if [[ "$use_cache" == "1" ]]; then
  docker compose build scout
else
  docker compose build --no-cache scout
fi

if ! grep -q '^VAPID_PRIVATE_KEY=.' "$env_file" || ! grep -q '^VAPID_PUBLIC_KEY=.' "$env_file"; then
  echo "Generating persistent VAPID keys for installed-app Web Push..."
  sed -i '/^VAPID_PRIVATE_KEY=/d;/^VAPID_PUBLIC_KEY=/d' "$env_file"
  docker compose run --rm --no-deps scout python /srv/scripts/generate-vapid.py >> "$env_file"
fi
if ! grep -q '^VAPID_SUBJECT=.' "$env_file"; then
  sed -i '/^VAPID_SUBJECT=/d' "$env_file"
  printf 'VAPID_SUBJECT=mailto:scout@localhost\n' >> "$env_file"
fi
docker compose up -d --force-recreate scout

healthy=0
for _ in $(seq 1 30); do
  if payload="$(curl -fsS http://127.0.0.1:18081/healthz 2>/dev/null)"; then
    reported="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' <<<"$payload")"
    if [[ "$reported" == "$version" ]]; then
      healthy=1
      break
    fi
  fi
  sleep 3
done

if [[ "$healthy" != "1" ]]; then
  echo "VPS health/version verification failed. Backup: $backup" >&2
  docker compose ps >&2 || true
  docker logs --since 5m --tail 150 stockhunter-scout >&2 || true
  exit 1
fi

curl -fsS http://127.0.0.1:18081/api/settings/scanner >/dev/null
curl -fsS http://127.0.0.1:18081/manifest.webmanifest | grep -q '"display": "standalone"'
curl -fsS http://127.0.0.1:18081/sw.js | grep -q "const VERSION=\"$version\""

universe_ready=0
for _ in $(seq 1 60); do
  payload="$(curl -fsS http://127.0.0.1:18081/healthz 2>/dev/null || true)"
  universe="$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("universe",0))' <<<"$payload" 2>/dev/null || echo 0)"
  if [[ "$universe" =~ ^[0-9]+$ ]] && (( universe > 0 )); then
    universe_ready=1
    break
  fi
  sleep 3
done

if [[ "$universe_ready" != "1" ]]; then
  echo "VPS version is healthy but the live universe remained empty. Backup: $backup" >&2
  docker logs --since 5m --tail 200 stockhunter-scout >&2 || true
  exit 1
fi

echo "VPS backend and PWA $version are healthy with $universe symbols. Backup: $backup"
