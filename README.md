# SPRECORDER

SPRECORDER is the custom web application built around the original ChanTrail Stripchat Recorder engine.

## Features
- Monitoring section with multiple models
- Stripchat profile images and live/offline status
- Independent live monitoring
- Manual recording to MP4
- Separate Auto Record section
- Automatic start when a model goes online
- Automatic stop and MP4 finalization when a model goes offline
- Persistent model/config/recording/log data on TrueNAS

## Docker
The image is published automatically to GitHub Container Registry:

`ghcr.io/salides/sprecorder:latest`

The container exposes:
- `8080` = SPRECORDER web interface
- `3030` = original ChanTrail relay/backend

Persistent TrueNAS paths:
- `/mnt/services/SPRECORDER/config` -> `/data/config`
- `/mnt/services/SPRECORDER/recordings` -> `/data/recordings`
- `/mnt/services/SPRECORDER/modules` -> `/data/modules`
- `/mnt/services/SPRECORDER/logs` -> `/data/logs`
