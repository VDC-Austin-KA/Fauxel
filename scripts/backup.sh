#!/usr/bin/env bash
# Nightly Postgres backup: pg_dump (in the db container) -> gzip -> upload to R2 (S3 API) -> prune after N days.
# Usage: scripts/backup.sh [--local-only]
# Config from infra/.env: POSTGRES_USER, POSTGRES_DB, BACKUP_S3_ENDPOINT, BACKUP_S3_BUCKET, BACKUP_S3_ACCESS_KEY,
# BACKUP_S3_SECRET_KEY, BACKUP_RETENTION_DAYS (default 14), optional BACKUP_DOCKER_NETWORK (only to reach a
# compose-internal endpoint such as http://s3:9000 when testing locally).
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
envfile="$root/infra/.env"
[ -f "$envfile" ] || { echo "missing $envfile" >&2; exit 1; }
get() { sed -n "s/^$1=//p" "$envfile" | tail -n1 | tr -d '\r'; }

PGUSER="$(get POSTGRES_USER)"; PGUSER="${PGUSER:-fauxel}"
PGDB="$(get POSTGRES_DB)"; PGDB="${PGDB:-fauxel}"
ENDPOINT="${BACKUP_S3_ENDPOINT:-$(get BACKUP_S3_ENDPOINT)}"
BUCKET="${BACKUP_S3_BUCKET:-$(get BACKUP_S3_BUCKET)}"; BUCKET="${BUCKET:-fauxel-backups}"
AK="${BACKUP_S3_ACCESS_KEY:-$(get BACKUP_S3_ACCESS_KEY)}"
SK="${BACKUP_S3_SECRET_KEY:-$(get BACKUP_S3_SECRET_KEY)}"
DAYS="${BACKUP_RETENTION_DAYS:-$(get BACKUP_RETENTION_DAYS)}"; DAYS="${DAYS:-14}"
NET="${BACKUP_DOCKER_NETWORK:-$(get BACKUP_DOCKER_NETWORK)}"
LOCAL_ONLY=0; [ "${1:-}" = "--local-only" ] && LOCAL_ONLY=1

outdir="${FAUXEL_BACKUP_DIR:-$root/backups}"; mkdir -p "$outdir"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
name="fauxel-${stamp}.sql.gz"
compose=(docker compose --project-directory "$root/infra" -f "$root/infra/docker-compose.yml")

echo "dumping $PGDB ..."
"${compose[@]}" exec -T db sh -c "pg_dump -U '$PGUSER' -d '$PGDB' --no-owner --clean --if-exists | gzip -9 > /tmp/$name"
"${compose[@]}" cp "db:/tmp/$name" "$outdir/$name"
"${compose[@]}" exec -T db rm -f "/tmp/$name"
size=$(wc -c < "$outdir/$name")
[ "$size" -gt 200 ] || { echo "dump suspiciously small ($size bytes)" >&2; rm -f "$outdir/$name"; exit 1; }
gzip -t "$outdir/$name"
echo "wrote $outdir/$name ($size bytes)"

aws() {
  local netargs=(); [ -n "$NET" ] && netargs=(--network "$NET")
  docker run --rm "${netargs[@]}" -v "$outdir:/backup" \
    -e AWS_ACCESS_KEY_ID="$AK" -e AWS_SECRET_ACCESS_KEY="$SK" -e AWS_DEFAULT_REGION=auto \
    -e AWS_REQUEST_CHECKSUM_CALCULATION=when_required -e AWS_RESPONSE_CHECKSUM_VALIDATION=when_required \
    amazon/aws-cli --endpoint-url "$ENDPOINT" "$@"
}

if [ "$LOCAL_ONLY" = 1 ] || [ -z "$ENDPOINT" ]; then
  echo "WARN: no BACKUP_S3_ENDPOINT (or --local-only): kept local copy only" >&2
else
  aws s3 cp "/backup/$name" "s3://$BUCKET/db/$name" --only-show-errors
  echo "uploaded s3://$BUCKET/db/$name"
  cutoff="$(date -u -d "-${DAYS} days" +%Y-%m-%dT%H:%M:%SZ)"
  old="$(aws s3api list-objects-v2 --bucket "$BUCKET" --prefix db/ --query "Contents[?LastModified<'${cutoff}'].Key" --output text | tr '\t' '\n' | grep -v '^None$' || true)"
  n=0
  while IFS= read -r key; do
    [ -n "$key" ] || continue
    aws s3api delete-object --bucket "$BUCKET" --key "$key" >/dev/null; n=$((n+1))
  done <<< "$old"
  echo "pruned $n remote backup(s) older than ${DAYS} days"
fi

# prune local copies too
find "$outdir" -maxdepth 1 -name 'fauxel-*.sql.gz' -mtime "+${DAYS}" -print -delete | sed 's/^/pruned local /'
echo "backup OK: $name"
