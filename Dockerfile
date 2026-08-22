# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

# 国内网络下 deb.debian.org 常超时，切换为阿里云 Debian 镜像。
# ocrmypdf 需要 tesseract-ocr 和中文语言包。
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list; \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
        tesseract-ocr-eng \
        poppler-utils \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 使用清华 PyPI 镜像
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

# 安装 uv
RUN pip install --no-cache-dir uv

ENV UV_HTTP_TIMEOUT=300 \
    OMP_THREAD_LIMIT=1 \
    OMP_NUM_THREADS=1

WORKDIR /app

# Layer 1: install dependencies (cached unless pyproject.toml/uv.lock changes)
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen --no-install-project

# Layer 2: copy source and install project
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --frozen

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["aether-mcp-server"]
CMD ["http", "--host", "0.0.0.0", "--port", "8000"]
