#!/usr/bin/env bash
# Nightly backup: dumps Postgres + tars the uploads volume, pushes both to
# Jetstream2 Swift object storage, then prunes backups older than RETENTION_DAYS.

set -euo pipefail

: "${APP_DIR:?APP_DIR must be set (path to this repo's checkout on this VM)}"
: "${OPENRC_FILE:?OPENRC_FILE must be set (path to the backup application credential's openrc file)}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
SWIFT_CONTAINER="${SWIFT_CONTAINER:-idea-prod-backups}"
STAGING_DIR="${STAGING_DIR:-/tmp}"

set -a
source "$APP_DIR/.env"
set +a
source "$OPENRC_FILE"

DATE="$(date +%F)"
DUMP_FILE="$STAGING_DIR/idea-db-$DATE.dump"
APPDATA_FILE="$STAGING_DIR/idea-appdata-$DATE.tgz"

cleanup() {
  rm -f "$DUMP_FILE" "$APPDATA_FILE"
}
trap cleanup EXIT

# Dump Postgres
cd "$APP_DIR"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > "$DUMP_FILE"

# Tar the uploads volume
UPLOADS_VOLUME="$(docker volume ls --format '{{.Name}}' | grep -E '_idea_persistent_data$')"
docker run --rm \
  --user "$(id -u):$(id -g)" \
  -v "$UPLOADS_VOLUME":/data \
  -v "$STAGING_DIR":/backup \
  alpine tar czf "/backup/idea-appdata-$DATE.tgz" -C /data .

# Upload both to Swift
openstack object create "$SWIFT_CONTAINER" "$DUMP_FILE" --name "idea-db-$DATE.dump"
openstack object create "$SWIFT_CONTAINER" "$APPDATA_FILE" --name "idea-appdata-$DATE.tgz"

echo "Backup uploaded: idea-db-$DATE.dump, idea-appdata-$DATE.tgz"

# Enforce retention
CUTOFF="$(date -d "-$RETENTION_DAYS days" +%F 2>/dev/null || date -v -"${RETENTION_DAYS}"d +%F)"
openstack object list "$SWIFT_CONTAINER" -f value -c Name | while read -r OBJ_NAME; do
  OBJ_DATE="$(echo "$OBJ_NAME" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}')"
  if [[ -n "$OBJ_DATE" && "$OBJ_DATE" < "$CUTOFF" ]]; then
    echo "Deleting old backup: $OBJ_NAME (older than $RETENTION_DAYS days)"
    openstack object delete "$SWIFT_CONTAINER" "$OBJ_NAME"
  fi
done