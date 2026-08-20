#!/bin/sh
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -g "$PGID" spindle
fi
if ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin spindle
fi
usermod -a -G cdrom "$(getent passwd "$PUID" | cut -d: -f1)" 2>/dev/null || true

mkdir -p "$SPINDLE_DATA_DIR" "$SPINDLE_WORK_DIR"
chown -R "$PUID:$PGID" "$SPINDLE_DATA_DIR" "$SPINDLE_WORK_DIR"

exec gosu "$PUID:$PGID" "$@"
