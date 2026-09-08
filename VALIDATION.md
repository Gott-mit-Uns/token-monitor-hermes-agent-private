# Upgrade validation — 2026-09-08

Target: DXP4800, existing device ID `Hermes-NAS-4800`.

- Built `token-monitor-hermes-agent:0.54.0-nas.1` successfully on the NAS.
- Ran 19 Agent tests in the built runtime image: all passed.
- Dependency pruning removed 16 packages; npm reported zero known vulnerabilities at build time.
- Old running container baseline: approximately 147 MiB of 512 MiB, 0 restarts.
- Repository visibility verified private; collaborator list contained only the owner.

Deployment is pending: the NAS SSH service became unavailable after tests and before
backup/dry-run/deployment. The existing v0.42.1 container has not been replaced.
Actual-data dry-run, state backup, post-deployment Hub receipt, and new-runtime
memory measurements must pass before this upgrade is reported as deployed.

The new health command distinguishes successful collection from successful upload.
Upload staleness is reported separately; it does not trigger restart loops while
the Hub is offline. No automatic device deletion is enabled.
