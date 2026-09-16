# ---- 前端构建（Vue3 + Vite + ECharts）
FROM node:22-slim AS web
WORKDIR /web
RUN npm i -g pnpm
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend ./
RUN pnpm build

# ---- 运行时（API + 内嵌调度，单容器单端口）
FROM python:3.12-slim
WORKDIR /app
ENV TZ=Asia/Shanghai UV_CACHE_DIR=/tmp/uv-cache
# uv 固定版本：CI 构建可复现，避免 latest 漂移（本地 uv 同为 0.11.13）
COPY --from=ghcr.io/astral-sh/uv:0.11.13 /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY scripts ./scripts
COPY --from=web /web/dist ./frontend/dist

# DATABASE_URL 由 Komodo 环境注入；看板/API 端口 8321
EXPOSE 8321
CMD ["uv", "run", "--no-sync", "python", "scripts/api.py", "--with-scheduler", "--port", "8321"]
