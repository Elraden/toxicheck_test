#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
root=/opt/toxicheck
release=${1:-$(readlink -f "$root/current")}
[[ "$release" =~ ^/opt/toxicheck/releases/[0-9a-f]{40}$ ]] || { echo 'Invalid release path' >&2; exit 1; }
export RELEASE_SHA=${release##*/}
compose=(docker compose --env-file "$root/.env" -f "$release/compose.yml")
backup="$root/backups/$(date -u +%Y%m%dT%H%M%SZ)-$$"
mkdir -p "$backup"
echo 'Creating database and research photo backup...'
"${compose[@]}" exec -T postgres pg_dump -U toxicheck -d toxicheck --no-owner --no-acl -Fc > "$backup/database.dump.partial"
"${compose[@]}" exec -T postgres pg_restore --list < "$backup/database.dump.partial" > /dev/null
mv "$backup/database.dump.partial" "$backup/database.dump"
# The helper mounts the volume read-only; it works even if API is stopped.
docker run --rm --network none --read-only --entrypoint python \
  -v toxicheck_research_photos:/photos:ro "toxicheck-api:$RELEASE_SHA" \
  -c 'import sys,tarfile; t=tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz"); t.add("/photos", arcname="."); t.close()' \
  > "$backup/research-photos.tar.gz.partial"
mv "$backup/research-photos.tar.gz.partial" "$backup/research-photos.tar.gz"
(cd "$backup" && sha256sum database.dump research-photos.tar.gz > SHA256SUMS)
printf '%s\n' "$RELEASE_SHA" > "$backup/release"
echo "Backup completed: $backup"
