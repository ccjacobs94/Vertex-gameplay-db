#!/bin/sh
set -e

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

    # Ensure /app/data directory exists and has correct ownership
    mkdir -p /app/data
    chown -R "$PUID:$PGID" /app/data 2>/dev/null || true

    if [ "$PUID" != "0" ]; then
        exec gosu "$PUID:$PGID" "$@"
    fi
fi

exec "$@"
