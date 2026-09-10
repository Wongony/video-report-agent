FROM node:22-bookworm-slim AS node
FROM ghcr.io/astral-sh/uv:0.12.4 AS uv
FROM python:3.12-slim-bookworm

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && npm install -g @earendil-works/pi-coding-agent@0.85.0 \
    && npm cache clean --force
COPY --from=uv /uv /uvx /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg ca-certificates fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PATH=/app/.venv/bin:$PATH \
    ASR_BACKEND=paraformer \
    ASR_MODEL=paraformer-v2
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
RUN uv run --no-sync playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache
COPY src ./src
COPY config/pi/models.json ./config/pi/models.json
# Editable install preserves the existing PROJECT_ROOT=/app convention.
RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/runs \
    && chown -R app:app /app /opt/playwright
USER app
EXPOSE 8765
STOPSIGNAL SIGINT
CMD ["video-report", "--runs", "/app/runs", "web", "--host", "0.0.0.0", "--mode", "public", "--port", "8765"]
