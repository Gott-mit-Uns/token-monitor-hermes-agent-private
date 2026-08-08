# Security notes

- Keep this repository and its linked GHCR package private.
- Do not commit `.env`, Hub secrets, GitHub tokens, Hermes databases, Agent state, logs, or usage exports.
- Use a dedicated read-only package credential on the NAS.
- Rotate a credential immediately if it appears in Git history, workflow logs, or Compose output.
- Review dependency and base-image updates before rebuilding the NAS image.
