# syntax=docker/dockerfile:1

FROM node:24-alpine AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# Vite はビルド時に VITE_* を埋め込むため、Firebase の設定はここで渡す必要がある。
# 未指定でもビルドは通り、その場合フロントは「開発モードで入る」（ヘッダ認証）になる。
ARG VITE_FIREBASE_API_KEY=""
ARG VITE_FIREBASE_AUTH_DOMAIN=""
ARG VITE_FIREBASE_PROJECT_ID=""
ENV VITE_FIREBASE_API_KEY=$VITE_FIREBASE_API_KEY \
    VITE_FIREBASE_AUTH_DOMAIN=$VITE_FIREBASE_AUTH_DOMAIN \
    VITE_FIREBASE_PROJECT_ID=$VITE_FIREBASE_PROJECT_ID
RUN npm run build

FROM python:3.12-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
# 本番のマイグレーションは同じイメージの Cloud Run Job（speclino-migrate）で流す。
COPY alembic.ini ./
COPY migrations/ ./migrations/
# sandbox グループ（pytest）も入れる。Cloud Run sandboxes は親コンテナの / を rootfs に
# するため、隔離実行に要るものはこのイメージに含まれている必要がある（S38）。
RUN uv sync --frozen --no-dev --group sandbox

COPY --from=web-build /web/dist /app/web/dist

ENV SPECLINO_ROLE=all
# `uv run` のたびに開発用の依存（dev グループ）を取りに行かない。依存は上の uv sync で固定済み
ENV UV_NO_SYNC=1
EXPOSE 8080
CMD ["uv", "run", "uvicorn", "speclino.api.app:app", "--host", "0.0.0.0", "--port", "8080"]
