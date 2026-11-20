#!/usr/bin/env bash
# Restore a backup made by backup.sh into the running db container (replaces objects in the target database).
# Usage: scripts/restore.sh backups/fauxel-20260101T020000Z.sql.gz     (stop api/relay first: docker compose stop api relay)
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
file="${1:?usage: restore.sh <file.sql.gz>}"
[ -f "$file" ] || { echo "no such file: $file" >&2; exit 1; }
envfile="$root/infra/.env"
get() { sed -n "s/^$1=//p" "$envfile" | tail -n1 | tr -d '\r'; }
PGUSER="$(get POSTGRES_USER)"; PGUSER="${PGUSER:-fauxel}"; PGDB="$(get POSTGRES_DB)"; PGDB="${PGDB:-fauxel}"
gzip -t "$file"
compose=(docker compose --project-directory "$root/infra" -f "$root/infra/docker-compose.yml")
gzip -dc "$file" | "${compose[@]}" exec -T db psql -v ON_ERROR_STOP=1 -q -U "$PGUSER" -d "$PGDB"
echo "restored $file into $PGDB"
