# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS dependencies

WORKDIR /opt/token-monitor
COPY app/package.json app/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force
# Only these inputs affect binary verification. Other build/test scripts must
# not invalidate the expensive dependency installation layer.
COPY app/scripts/ensure-vendored-tokscale.js app/scripts/vendoredTokscale.js ./scripts/
COPY app/scripts/vendor/tokscale.json ./scripts/vendor/tokscale.json
RUN npm run ensure:tokscale && npm pkg delete dependencies.electron-updater 'dependencies.@xhayper/discord-rpc' && npm prune --omit=dev && npm cache clean --force

FROM node:22-bookworm-slim@sha256:83f487e0a63425e5b4d146fb5e5be574bcbe1b7b843d3ebafdd95eaf7767a7e5 AS runtime

ARG BUILD_VERSION=v0.65.0-03
ARG VCS_REF=unknown
ENV NODE_ENV=production TOKEN_MONITOR_NAS_VERSION=${BUILD_VERSION}
WORKDIR /opt/token-monitor

LABEL org.opencontainers.image.title="Token Monitor Hermes Agent" \
      org.opencontainers.image.description="NAS image for Hermes token monitoring" \
      org.opencontainers.image.source="https://github.com/Gott-mit-Uns/token-monitor-nas" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="MIT"

COPY app/package.json app/package-lock.json ./
COPY app/src/agent ./src/agent
COPY app/src/shared ./src/shared
RUN chmod -R a=rX ./package.json ./package-lock.json ./src
COPY --from=dependencies --link /opt/token-monitor/node_modules ./node_modules

CMD ["node", "src/agent/agent.js"]
