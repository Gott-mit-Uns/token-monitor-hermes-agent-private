# Upgrade validation — 2026-09-08

Target: DXP4800, existing device ID `Hermes-NAS-4800`.

- Built `token-monitor-hermes-agent:0.54.0-nas.1` successfully on the NAS.
- Ran 19 Agent tests in the built runtime image: all passed.
- Dependency pruning removed 16 packages; npm reported zero known vulnerabilities at build time.
- Old running container baseline: approximately 147 MiB of 512 MiB, 0 restarts.
- Repository visibility verified private; collaborator list contained only the owner.

Deployment completed on 2026-09-08 after the SSH session was restored.
- Consistent state and original configuration/source backup completed before cutover.
- A dry run against the Hermes database, using an independent state copy, succeeded.
- Today, month and all-time token totals matched the old Agent's Hub record exactly.
- The existing Compose path and device ID were retained.
- Hub received version 0.54.0 with Hermes active after cutover.
- Local collection and upload checks both reported ok; Docker health was healthy.
- Restart count was zero. Post-start memory snapshot: 118.4 MiB of 512 MiB, CPU 0%.
  This is a snapshot, not a long-term performance benchmark.
- Old image, original files and state snapshots remain on the NAS for rollback.

The new health command distinguishes successful collection from successful upload.
Upload staleness is reported separately; it does not trigger restart loops while
the Hub is offline. No automatic device deletion is enabled.
