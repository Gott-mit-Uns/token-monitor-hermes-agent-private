# Token Monitor Hermes Agent (private NAS build)

Private, reproducible Docker deployment of Token Monitor's Hermes collector for the NAS.

## What is included

- Read-only root runtime for the NAS ACL, with only DAC_READ_SEARCH restored.
- Read-only directory mount for Hermes SQLite/WAL consistency.
- Separate successful collection and upload timestamps, without storing payloads or credentials.
- Five-minute collection fallback and 30-second filesystem-event debounce.
- Fixed device ID: changing it creates a different Hub record; no automatic deletion.
- Locally built image; GitHub Actions validates the build without publishing an image.

## Secrets and runtime data

Copy `.env.example` to `.env` on the NAS and provide the real Hub URL and secret locally. Never commit `.env`, Hermes databases, Agent state, usage data, or backup archives.

## Start or update

```sh
docker compose build
docker compose up -d token-monitor-hermes-agent
```

Changing `TOKEN_MONITOR_DEVICE_ID` requires Compose recreation; `docker restart` and `docker compose restart` do not reload changed environment values.

## Image

```text
token-monitor-hermes-agent:0.54.0-nas.1
```

The GitHub repository must remain private. No credentials or runtime data belong in Git.

## Status and recovery

Run `docker exec token-monitor-hermes-agent node src/agent/nasHealth.js`.
The output separately reports `collection` and `upload` as `ok` or `stale`.
The default stale threshold is 15 minutes, or three collection intervals if longer.
Docker health fails for a stalled collector, unreadable database, or missing process.
A stale upload alone does not fail Docker health: a sleeping Mac must not cause restart loops.
Docker does not automatically restart an unhealthy container; diagnose local stalls separately.

Before an upgrade, stop the agent briefly and back up Compose, Dockerfile, source and state;
retain the old image. Restore both configuration and state when rolling back.
Keep `docker-compose.yaml` at the original path for UGREEN project management.
The `.env` is maintained only on the NAS. Reserve a stable LAN address for the Hub.

## Build provenance

Upstream v0.54.0, commit `fce070c789ae8b1ca59be3ce7c09fd8301d6f631`.
Node 22 Bookworm slim is pinned by digest in the Dockerfile.
The build runs the official `ensure:tokscale` step (v0.54.0 uses the upstream npm binary).
Desktop updater and Discord dependencies are pruned; shared native dependencies remain.
The runtime stays read-only and does not install anything at startup.

## Upstream

Based on [Javis603/token-monitor](https://github.com/Javis603/token-monitor), licensed under MIT. Local NAS-specific changes are maintained in this private repository.
