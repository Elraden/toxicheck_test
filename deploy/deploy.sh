#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
root=/opt/toxicheck
revision=${1:?Usage: bash deploy.sh FULL_COMMIT_SHA}
[[ "$revision" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected a full commit SHA' >&2; exit 1; }
[[ "$(uname -m)" == x86_64 ]] || { echo 'CI images require an x86_64 server' >&2; exit 1; }
release="$root/releases/$revision"
[[ -f "$root/.env" && -f "$release/compose.yml" ]] || { echo 'Prepare /opt/toxicheck/.env and release first' >&2; exit 1; }
mkdir -p "$root/backups"
exec 9>"$root/deploy.lock"
flock -n 9 || { echo 'Another deployment is running' >&2; exit 1; }

# Read values as data, never execute the environment file as a shell script.
site=$(sed -n 's/^SITE_ADDRESS=//p' "$root/.env")
password=$(sed -n 's/^POSTGRES_PASSWORD=//p' "$root/.env")
[[ "$site" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ ]] || { echo 'Invalid SITE_ADDRESS' >&2; exit 1; }
[[ "$password" =~ ^[0-9a-fA-F]{64}$ ]] || { echo 'POSTGRES_PASSWORD must contain 64 hex characters' >&2; exit 1; }
unset password
export RELEASE_SHA="$revision"
compose=(docker compose --env-file "$root/.env" -f "$release/compose.yml")
"${compose[@]}" config --quiet

if [[ -f "$release/images.tar.gz" ]]; then
  (cd "$release" && sha256sum -c SHA256SUMS)
  docker load -i "$release/images.tar.gz"
fi
docker image inspect "toxicheck-api:$revision" "toxicheck-web:$revision" >/dev/null
previous=$(readlink -f "$root/current" || true)
"${compose[@]}" up -d --wait --wait-timeout 120 postgres
bash "$release/backup.sh" "$release"
"${compose[@]}" run --rm --no-deps api python -m app.db.initialize --expected-host postgres --apply

rollback() {
  trap - ERR
  echo 'Deployment failed. Database backup is in /opt/toxicheck/backups.' >&2
  if [[ "$previous" =~ ^/opt/toxicheck/releases/[0-9a-f]{40}$ && "$previous" != "$release" ]]; then
    echo "Restoring application images from ${previous##*/} (database is not rolled back)." >&2
    RELEASE_SHA="${previous##*/}" docker compose --env-file "$root/.env" -f "$previous/compose.yml" \
      up -d --wait --wait-timeout 240 api web || echo 'Automatic rollback failed; inspect docker compose logs.' >&2
  fi
  exit 1
}
trap rollback ERR
"${compose[@]}" up -d --wait --wait-timeout 240 api web
curl --fail --silent --show-error --retry 12 --retry-all-errors --retry-delay 5 \
  --connect-timeout 10 --max-time 20 "https://$site/api/health/db" >/dev/null
curl --fail --silent --show-error --connect-timeout 10 --max-time 20 "https://$site/" >/dev/null
trap - ERR
if [[ "$previous" =~ ^/opt/toxicheck/releases/[0-9a-f]{40}$ && "$previous" != "$release" ]]; then
  ln -sfn "$previous" "$root/previous"
fi
ln -sfn "$release" "$root/current"
# Remove only the verified transport archive, retaining images for rollback.
if [[ -f "$release/images.tar.gz" ]]; then rm -- "$release/images.tar.gz"; fi
echo "Deployed $revision at https://$site"
