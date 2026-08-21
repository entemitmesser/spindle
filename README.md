# Spindle

Self-hosted automatic disc ripper. It watches every optical drive on the host, rips
inserted discs, transcodes them, fetches metadata and subtitles, and files the result
into a Jellyfin-compatible library — with a web UI for monitoring and control.

See `CLAUDE.md` and `docs/architecture.md` for the full technical contract, and
`docs/PLAN.md` for the phased build order.

## Legal note

Circumventing disc copy protection is restricted in several jurisdictions, including
Germany (§95a UrhG), regardless of whether you own the disc. **Spindle ships no
decryption code and no keys.** MakeMKV and any `libdvdcss` use are optional, externally
installed backends Spindle discovers at runtime — if they're absent, unencrypted discs
still work and the UI just says so. You are responsible for what you install alongside
Spindle and for complying with the law in your jurisdiction.

## Quick start

Requires Docker and at least one optical drive passed through to the host.

```bash
mkdir spindle && cd spindle
curl -O https://raw.githubusercontent.com/<you>/spindle/main/docker-compose.yml
docker compose up -d
```

Or write the compose file yourself — this is the complete one:

```yaml
services:
  spindle:
    image: ghcr.io/<you>/spindle:latest
    ports:
      - "8478:8478"
    environment:
      PUID: "1000" # match the user that should own files in ./data, ./work, ./library
      PGID: "1000"
      TZ: "Etc/UTC"
      SPINDLE_LOG_LEVEL: INFO
      # SPINDLE_AUTH_ENABLED: "true"        # password-protect the UI, see below
      # SPINDLE_TMDB_API_KEY: ""
      # SPINDLE_OPENSUBTITLES_API_KEY: ""
      # SPINDLE_JELLYFIN_URL: "http://jellyfin:8096"
      # SPINDLE_JELLYFIN_TOKEN: ""
    volumes:
      - ./data:/data # spindle.db, logs, secret.key, custom presets
      - ./work:/work # scratch space for in-progress rips/transcodes -- put this on an SSD
      - ./library:/library # final Jellyfin-compatible output
      - /run/udev:/run/udev:ro # udev metadata, for reliable drive model/vendor detection
    devices:
      - "/dev/sr0:/dev/sr0" # one line per optical drive: add /dev/sr1, /dev/sr2, ...
    group_add:
      - cdrom
    restart: unless-stopped
```

Open `http://<host>:8478`, insert a disc, and it starts ripping automatically (toggle
`behaviour.auto_start` in Settings if you'd rather queue manually). MakeMKV needs its
own license key (`SPINDLE_MAKEMKV_KEY`) for encrypted Blu-rays/UHDs — unencrypted discs
and DVDs work without one.

### Auth

Off by default (`SPINDLE_AUTH_ENABLED=false`) — Spindle binds `0.0.0.0` and assumes a
trusted LAN. Set `SPINDLE_AUTH_ENABLED=true` to require a password: the first visit
prompts you to set one, after which every page and API call needs a session cookie.
Sessions last 30 days (sliding) and survive a restart via `data_dir/secret.key`
(generated once, mode `0600`) unless you set `SPINDLE_SECRET_KEY` yourself.

## Hardware notes

- **Drive**: any SATA/USB Blu-ray drive rips DVDs and unencrypted Blu-rays. **UHD
  Blu-ray** needs a drive with a UHD-friendly firmware and MakeMKV's UHD support (which
  in turn needs a purchased MakeMKV key for most UHD discs) — check MakeMKV's own
  hardware compatibility notes before buying a drive specifically for UHD.
- **Multiple drives**: pass through as many `/dev/srN` devices as you have; Spindle
  rips one disc per drive concurrently (ripping and transcoding also run in parallel
  across jobs — see `docs/architecture.md` §7).
- **Scratch volume (`/work`)**: put this on an SSD if at all possible. A rip writes the
  full disc image (up to ~100 GB for a UHD) and the transcode reads it back
  immediately after — on spinning disk this is the single biggest throughput bottleneck
  in the whole pipeline. Sizing formula: `(largest disc you'll rip) × (concurrent
  jobs + 1)`, e.g. 2 concurrent UHD jobs → budget ~300 GB.
- **Hardware encoding**: `transcode.hardware_encoder: auto` picks the first of
  QSV/NVENC/VAAPI that HandBrake reports as available in the container; `none` forces
  software (`x265`/`x264`), which is much slower but needs no GPU passthrough. Check
  `GET /api/v1/system` (`encoders`) to see what HandBrake actually detected.
- **arm64**: the published image is multi-arch, but MakeMKV has no official arm64
  build and isn't bundled in the image on either architecture (it's an optional,
  externally-installed backend either way — `CLAUDE.md` §9). On amd64 you can install
  it into the container yourself; on arm64 there's currently no MakeMKV path at all, so
  disc ripping is limited to whatever HandBrake alone can do.

## Troubleshooting

**Drive not detected** — check `docker logs` for `OpticalAdapter` errors; confirm the
`devices:` entry matches the drive's actual `/dev/srN` path on the host (it can shift
across reboots) and that `group_add: cdrom` is present. `GET /api/v1/drives` lists what
Spindle currently sees.

**"MakeMKV key expired" / rips fail immediately** — MakeMKV's free beta key rotates
periodically; get a current one from MakeMKV's forum and set `SPINDLE_MAKEMKV_KEY`.
Unencrypted discs (most DVDs, some Blu-rays) work with no key at all.

**Permission errors writing to `/library`** — `PUID`/`PGID` must match a user that can
write to the host directories bind-mounted at `/data`/`/work`/`/library`; `library.
file_mode`/`dir_mode` in Settings control what Spindle chmods files to after moving
them in, but it never chowns (no capability to do so without running as root).

**Jellyfin isn't picking up new files** — confirm `SPINDLE_JELLYFIN_URL`/`_TOKEN` are
set and `POST /api/v1/settings/test/jellyfin` succeeds; a reachability failure there is
always a `system.warning`, surfaced as a banner in the UI, not a silent no-op.

**No hardware encoder found** — check `GET /api/v1/system`'s `encoders` list; if it's
empty, HandBrake didn't detect QSV/NVENC/VAAPI in the container (usually missing GPU
device passthrough) and `hardware_encoder: auto` will fall back to software encoding,
which still works, just slower.

**For any issue report**, include the output of `GET /api/v1/system` — it has backend
availability, detected encoders, disk space and recent warnings in one place.

## Backup & restore

```bash
docker compose exec spindle spindle backup -o /data/backup.zip   # spindle.db + settings + presets
docker compose cp spindle:/data/backup.zip ./backup.zip

docker compose exec spindle spindle restore /data/backup.zip     # overwrites the live db; stop first
docker compose exec spindle spindle scan-library                 # adopt files copied in by hand
```

Or `POST /api/v1/system/backup` from the UI/API for a one-off download without a shell.

## Configuration reference

Two layers (`docs/CLAUDE.md` §6): **`Settings`**, below, are environment variables fixed
at container boot. Everything else — quality presets, naming templates, worker counts —
is **runtime config**, changeable live from the Settings page or `PUT /api/v1/settings`;
see that page (or `spindle/config.py::RuntimeConfig`) for the full, current set rather
than duplicating it here, since it's large and changes often.

| Variable | Default | Notes |
|---|---|---|
| `SPINDLE_DATA_DIR` | `/data` | `spindle.db`, logs, `secret.key`, custom presets |
| `SPINDLE_WORK_DIR` | `/work` | scratch space for in-progress rips/transcodes |
| `SPINDLE_LIBRARY_DIR` | `/library` | final Jellyfin-compatible output |
| `SPINDLE_HOST` | `0.0.0.0` | |
| `SPINDLE_PORT` | `8478` | |
| `SPINDLE_LOG_LEVEL` | `INFO` | |
| `SPINDLE_LOG_JSON` | `false` | structured JSON logs instead of console output |
| `SPINDLE_AUTH_ENABLED` | `false` | password-protect the whole UI/API |
| `SPINDLE_SECRET_KEY` | *(generated)* | session-cookie signing key; set it to keep sessions valid across a `data_dir` reset |
| `SPINDLE_METRICS_ENABLED` | `false` | exposes `GET /metrics` (Prometheus text) |
| `SPINDLE_TMDB_API_KEY` | *(unset)* | required for identification |
| `SPINDLE_OPENSUBTITLES_API_KEY` | *(unset)* | required for subtitle download |
| `SPINDLE_JELLYFIN_URL` / `SPINDLE_JELLYFIN_TOKEN` | *(unset)* | for the post-organize library refresh |
| `SPINDLE_MAKEMKV_KEY` | *(unset)* | needed for most encrypted UHD discs |

## Development

```bash
uv sync
uv run uvicorn spindle.main:create_app --factory --reload --port 8478

cd web
pnpm install
pnpm dev
```

`spindle/cli.py` also exposes `spindle serve` as an alternative to the `uvicorn` command
above, plus `migrate`/`backup`/`restore`/`scan-library` for offline maintenance.
