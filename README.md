# SPRECORDER Robust V2 — TrueNAS

## Why V2 exists

The previous single-container image `ghcr.io/salides/sprecorder:latest` exited with code **139** on TrueNAS (SIGSEGV). The image also bundled the Rust ChanTrail backend, Python, and FFmpeg into one process namespace.

V2 deliberately separates them:

- `chantrail` = official `chantrail/stripchat-recorder:latest` backend on port 3030.
- `sprecorder` = Python/FFmpeg recorder UI only, on port 8080.

This prevents a native crash in the backend from taking down the recorder UI and avoids rebuilding/copying the Rust binary into the Python image.

## TrueNAS paths

```text
/mnt/services/SPRECORDER/config
/mnt/services/SPRECORDER/recordings
/mnt/services/SPRECORDER/modules
/mnt/services/SPRECORDER/logs
```

## Ports

- `8080` → SPRECORDER UI
- `3030` → ChanTrail backend/relay

Open:

```text
http://TRUENAS-IP:8080
```

## Important

Do not delete the existing SPRECORDER datasets. The compose file reuses the existing `/mnt/services/SPRECORDER/...` paths.

The recorder uses the backend relay:

```text
http://chantrail:3030/stream/<model>
```

The browser player continues to use:

```text
http://TRUENAS-IP:3030/stream/<model>
```

## GHCR

The included workflow publishes:

```text
ghcr.io/salides/sprecorder-ui:latest
```

for `linux/amd64`.

## Tests

The recorder logic from the previous robust package is retained:
- one logical `.ts.part` per recording
- FFmpeg restart/reconnect into the same `.ts.part`
- stall detection
- terminal failure keeps `.ts.part`
- recovery of `.ts.part`
- duplicate-start protection
- independent models

