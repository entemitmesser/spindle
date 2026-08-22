#!/bin/sh
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
TZ="${TZ:-Etc/UTC}"

if [ -f "/usr/share/zoneinfo/$TZ" ]; then
    ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime
    echo "$TZ" > /etc/timezone
fi

if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -g "$PGID" spindle
fi
if ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -u "$PUID" -g "$PGID" -M -s /usr/sbin/nologin spindle
fi
SPINDLE_USER="$(getent passwd "$PUID" | cut -d: -f1)"

# `--device /dev/srN:/dev/srN` bind-mounts keep the HOST's numeric owning
# GID on the device node -- it is not remapped into the container. The
# image's own `cdrom` group (created at build time with no fixed GID) will
# only grant access if that GID happens to match by coincidence, which
# varies by host distro. So: look up whatever GID actually owns each mapped
# drive right now, and join it directly by number.
for dev in /dev/sr*; do
    [ -e "$dev" ] || continue
    dev_gid="$(stat -c '%g' "$dev")"
    if ! getent group "$dev_gid" >/dev/null 2>&1; then
        groupadd -g "$dev_gid" "disc-$dev_gid" 2>/dev/null || true
    fi
    usermod -a -G "$dev_gid" "$SPINDLE_USER" 2>/dev/null || true
done
# Also try the image's `cdrom` group by name, in case it does match.
usermod -a -G cdrom "$SPINDLE_USER" 2>/dev/null || true

mkdir -p "$SPINDLE_DATA_DIR" "$SPINDLE_WORK_DIR"
chown -R "$PUID:$PGID" "$SPINDLE_DATA_DIR" "$SPINDLE_WORK_DIR"

exec gosu "$PUID:$PGID" "$@"
