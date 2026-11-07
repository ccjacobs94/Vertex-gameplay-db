#!/bin/sh
set -e
umask 077

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

# Adjust user/group if running as root
if [ "$(id -u)" = "0" ]; then
    # Ensure group exists
    if ! getent group "$PGID" >/dev/null 2>&1; then
        groupadd -g "$PGID" appgroup 2>/dev/null || true
    fi
    # Ensure user exists
    if ! getent passwd "$PUID" >/dev/null 2>&1; then
        useradd -u "$PUID" -g "$PGID" -d /app -s /bin/sh -M appuser 2>/dev/null || true
    fi

    # Ensure application storage, backup, and cache directories exist with correct ownership
    mkdir -p /app/data /app/backups /app/cache
    chown -R "$PUID:$PGID" /app/data /app/backups /app/cache 2>/dev/null || true

    if [ "$PUID" != "0" ]; then
        exec gosu "$PUID:$PGID" "$@"
    fi
fi

exec "$@"
