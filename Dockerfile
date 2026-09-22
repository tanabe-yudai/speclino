# syntax=docker/dockerfile:1

FROM node:24-alpine AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev

COPY --from=web-build /web/dist /app/web/dist

ENV SPECLINO_ROLE=all
EXPOSE 8080
CMD ["uv", "run", "uvicorn", "speclino.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
