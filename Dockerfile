FROM python:3.12-slim

WORKDIR /app
ENV TZ=Asia/Shanghai UV_CACHE_DIR=/tmp/uv-cache

# uv 包管理（与本地开发一致）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 先只拷依赖清单，最大化构建缓存命中
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# 代码：scripts 自带 sys.path 处理，无需安装项目本体
COPY src ./src
COPY scripts ./scripts

# DATABASE_URL 由 Komodo 环境注入（不要打进镜像）
CMD ["uv", "run", "--no-sync", "python", "scripts/serve.py", "--catchup"]
