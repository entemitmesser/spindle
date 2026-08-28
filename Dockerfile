FROM node:25-alpine AS web
WORKDIR /web
RUN corepack enable
COPY web/package.json web/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile
COPY web/ ./
RUN pnpm build

FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install --no-install-recommends -y \
    handbrake-cli \
    ffmpeg \
    mkvtoolnix \
    libbluray2 \
    lsdvd \
    eject \
    udev \
    gosu \
    curl \
    tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && (getent group cdrom || groupadd cdrom)

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY spindle/ ./spindle/
COPY alembic.ini ./
RUN uv sync --frozen --no-dev

COPY --from=web /web/dist ./web/dist
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH" \
    SPINDLE_DATA_DIR=/data \
    SPINDLE_WORK_DIR=/work \
    SPINDLE_LIBRARY_DIR=/library

VOLUME ["/data", "/work", "/library"]
EXPOSE 8478

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8478/api/v1/system/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "spindle.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8478"]
