# syntax=docker/dockerfile:1.7

ARG ENGINE_IMAGE=ghcr.io/mhuot/skills-video-engine:0.2.0

FROM node:22-bookworm-slim AS web-build
ARG PNPM_VERSION=10.34.5
ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /src
RUN npm install --global "pnpm@${PNPM_VERSION}" --registry="${NPM_REGISTRY}" \
    && pnpm config set registry "${NPM_REGISTRY}"
COPY apps/web/package.json apps/web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY apps/web/ ./
RUN pnpm build

FROM nginx:1.29-alpine AS web
LABEL org.opencontainers.image.title="Skills Video Studio Web" \
      org.opencontainers.image.source="https://github.com/mhuot/skills-video-studio" \
      org.opencontainers.image.licenses="MIT"
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=web-build /src/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1:8080/health || exit 1

FROM ${ENGINE_IMAGE} AS api
ARG PYPI_INDEX=https://pypi.org/simple
USER root
LABEL org.opencontainers.image.title="Skills Video Studio API" \
      org.opencontainers.image.source="https://github.com/mhuot/skills-video-studio" \
      org.opencontainers.image.licenses="MIT"
COPY services/api /opt/studio-api
RUN UV_DEFAULT_INDEX="${PYPI_INDEX}" uv pip install --python /opt/venv/bin/python /opt/studio-api \
    && groupadd --gid 10001 studio \
    && useradd --uid 10001 --gid studio --home-dir /tmp --no-create-home studio \
    && mkdir -p /workspace /data /opt/studio-web \
    && chown -R studio:studio /workspace /data /opt/studio-web
ENV STUDIO_WORKSPACE_ROOT=/workspace \
    STUDIO_DATA_ROOT=/data \
    STUDIO_ENGINE_VERSION=0.2.0 \
    STUDIO_MAX_CONCURRENT_JOBS=1 \
    PYTHONUNBUFFERED=1
EXPOSE 8000
USER studio
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1
CMD ["uvicorn", "studio_api.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM api AS all-in-one
LABEL org.opencontainers.image.title="Skills Video Studio" \
      org.opencontainers.image.source="https://github.com/mhuot/skills-video-studio" \
      org.opencontainers.image.licenses="MIT"
COPY --from=web-build --chown=studio:studio /src/dist /opt/studio-web
ENV STUDIO_WEB_ROOT=/opt/studio-web
