# Token Monitor Hermes Agent (private NAS build)

Private, reproducible Docker deployment of Token Monitor's Hermes collector for the NAS.

## What is included

- Rootless, read-only runtime with all Linux capabilities dropped.
- Read-only directory mount for Hermes SQLite/WAL consistency.
- Business-level health heartbeat.
- Semantic payload deduplication with a five-minute heartbeat.
- Throttled Agent state writes.
- Automatic Device ID migration: the new ID is posted successfully before the old ID is retired.
- A private GHCR build published by GitHub Actions.

## Secrets and runtime data

Copy `.env.example` to `.env` on the NAS and provide the real Hub URL and secret locally. Never commit `.env`, Hermes databases, Agent state, usage data, or backup archives.

## Start or update

```sh
docker login ghcr.io
docker compose pull
docker compose up -d --force-recreate token-monitor-hermes-agent
```

Changing `TOKEN_MONITOR_DEVICE_ID` requires Compose recreation; `docker restart` and `docker compose restart` do not reload changed environment values.

## Image

```text
ghcr.io/gott-mit-uns/token-monitor-hermes-agent:0.42.0-nas.2
```

The GitHub repository and GHCR package must both remain private.

## Upstream

Based on [Javis603/token-monitor](https://github.com/Javis603/token-monitor), licensed under MIT. Local NAS-specific changes are maintained in this private repository.
