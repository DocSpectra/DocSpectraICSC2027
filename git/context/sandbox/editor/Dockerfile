# Sprint 11 editor image: pinned Node (locally cached alpine base, by digest) and
# pinned Codex CLI. Codex is installed without lifecycle scripts; the native musl
# binary arrives in the platform optional-dependency package. No system or user
# Codex config is baked in: /etc/codex and the user's ~/.codex do not exist, and
# each session gets a fresh CODEX_HOME on tmpfs.
FROM node@sha256:c921b97d4b74f51744057454b306b418cf693865e73b8100559189605f6955b8
ARG CODEX_VERSION=0.161.0
RUN npm install -g --ignore-scripts --no-audit --no-fund "@openai/codex@${CODEX_VERSION}" \
 && npm cache clean --force \
 && rm -rf /etc/codex /root/.codex \
 && addgroup -g 10001 editor && adduser -D -u 10001 -G editor -h /home/editor editor \
 && mkdir /work && chown 10001:10001 /work
USER 10001:10001
ENV CODEX_HOME=/codex-home HOME=/home/editor
