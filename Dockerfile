FROM node@sha256:0557ac14e0d45d02ed563067b82856ca5e7aa3437fa28d98d4350ea9c3d9494a AS dependencies

WORKDIR /opt/token-monitor
COPY app/package.json app/package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

FROM node:22-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436

ARG BUILD_VERSION=0.42.0-nas.2
ARG VCS_REF=unknown
ENV NODE_ENV=production
WORKDIR /opt/token-monitor

LABEL org.opencontainers.image.title="Token Monitor Hermes Agent" \
      org.opencontainers.image.description="Private NAS image for Hermes token monitoring" \
      org.opencontainers.image.source="https://github.com/Gott-mit-Uns/token-monitor-hermes-agent-private" \
      org.opencontainers.image.version="${BUILD_VERSION}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.licenses="MIT"

COPY app/package.json app/package-lock.json ./
COPY app/src/agent ./src/agent
COPY app/src/shared ./src/shared
RUN chmod -R a=rX ./package.json ./package-lock.json ./src
COPY --from=dependencies /opt/token-monitor/node_modules ./node_modules

CMD ["node", "src/agent/agent.js"]
