#!/bin/sh
set -eu

: "${APP_DB_USER:?APP_DB_USER is required}"
: "${APP_DB_PASSWORD:?APP_DB_PASSWORD is required}"
: "${MYSQL_DATABASE:?MYSQL_DATABASE is required}"
: "${MIGRATION_DB_USER:?MIGRATION_DB_USER is required}"
: "${MIGRATION_DB_PASSWORD:?MIGRATION_DB_PASSWORD is required}"

case "$APP_DB_USER" in
  *[!A-Za-z0-9_]*|'') echo "mysql-init: APP_DB_USER contains invalid characters" >&2; exit 1 ;;
esac

case "$MYSQL_DATABASE" in
  *[!A-Za-z0-9_]*|'') echo "mysql-init: MYSQL_DATABASE contains invalid characters" >&2; exit 1 ;;
esac

if [ "${#APP_DB_USER}" -gt 32 ]; then
  echo "mysql-init: APP_DB_USER too long" >&2
  exit 1
fi

if [ "${#MYSQL_DATABASE}" -gt 64 ]; then
  echo "mysql-init: MYSQL_DATABASE too long" >&2
  exit 1
fi

case "$MIGRATION_DB_USER" in
  *[!A-Za-z0-9_]*|'') echo "mysql-init: migration account contains invalid characters" >&2; exit 1 ;;
esac
if [ "${#MIGRATION_DB_USER}" -gt 32 ] || [ "$MIGRATION_DB_USER" = "root" ]; then
  echo "mysql-init: invalid migration account" >&2; exit 1
fi

if [ "$APP_DB_USER" = "${MIGRATION_DB_USER:-}" ] || [ "$APP_DB_USER" = "root" ]; then
  echo "mysql-init: application account must differ from migration/root account" >&2; exit 1
fi

escaped_migration_password=$(printf '%s' "$MIGRATION_DB_PASSWORD" | sed "s/'/''/g")
escaped_password=$(printf '%s' "$APP_DB_PASSWORD" | sed "s/'/''/g")

# Schema GRANT names are patterns unless partial_revokes is enabled.
# Read the mode, never change it; backslashes are literal when it is enabled.
partial_revokes=$(MYSQL_PWD="${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is required}" mysql --protocol=socket -uroot -Nse 'SELECT @@GLOBAL.partial_revokes')
case "$partial_revokes" in
  0) grant_database=$(printf '%s' "$MYSQL_DATABASE" | sed 's/_/\\_/g') ;;
  1) grant_database=$MYSQL_DATABASE ;;
  *) echo "mysql-init: unsupported privilege mode" >&2; exit 1 ;;
esac

MYSQL_PWD="${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is required}" mysql --protocol=socket -uroot <<SQL
SET SESSION sql_mode = CONCAT(@@sql_mode, ',NO_BACKSLASH_ESCAPES');
CREATE USER IF NOT EXISTS '${MIGRATION_DB_USER}'@'%' IDENTIFIED BY '${escaped_migration_password}';
ALTER USER '${MIGRATION_DB_USER}'@'%' IDENTIFIED BY '${escaped_migration_password}';
GRANT ALL PRIVILEGES ON \`${grant_database}\`.* TO '${MIGRATION_DB_USER}'@'%';
CREATE USER IF NOT EXISTS '${APP_DB_USER}'@'%' IDENTIFIED BY '${escaped_password}';
ALTER USER '${APP_DB_USER}'@'%' IDENTIFIED BY '${escaped_password}';
GRANT SELECT, INSERT, UPDATE, DELETE ON \`${grant_database}\`.* TO '${APP_DB_USER}'@'%';
SQL

echo "mysql-init: application user ready"

