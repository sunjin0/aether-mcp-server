# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

# 系统运行库只依赖基础镜像，置于最前以便业务代码和 Python 依赖变更时复用缓存。
# OpenCV（由 Docling 表格模型使用）在 slim 镜像中需要这些运行时库。
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libxcb1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Layer 1: install dependencies (cached unless pyproject.toml/uv.lock changes)
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen --no-install-project

# Layer 2: copy source and install project
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen

ENV HF_ENDPOINT=https://huggingface.co \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["aether-mcp-server"]
CMD ["http", "--host", "0.0.0.0", "--port", "8000"]
