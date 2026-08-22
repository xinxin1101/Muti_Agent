FROM node:24-bookworm-slim

ENV NODE_ENV=test \
    NPM_CONFIG_AUDIT=false \
    NPM_CONFIG_FUND=false \
    NPM_CONFIG_UPDATE_NOTIFIER=false

WORKDIR /workspace
