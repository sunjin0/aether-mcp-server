# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

# 系统运行库只依赖基础镜像，置于最前以便业务代码和 Python 依赖变更时复用缓存。
# OpenCV（由 Docling 表格模型使用）在 slim 镜像中需要这些运行时库。
# 国内网络下 deb.debian.org 常超时，切换为阿里云 Debian 镜像。
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libxcb1 \
    && rm -rf /var/lib/apt/lists/*

# files.pythonhosted.org 在国内下载慢且易超时；切换为清华 PyPI 镜像。
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=120
RUN pip install --no-cache-dir uv

# Docling 的可选 GPU 依赖较大；默认 30 秒下载超时在受限网络中不足。
# 文档解析服务不需要 GPU，因此构建时固定 CPU PyTorch 后端。
ENV UV_HTTP_TIMEOUT=300 \
    UV_TORCH_BACKEND=cpu

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
