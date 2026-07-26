FROM python:3.11-slim

RUN pip install --no-cache-dir uv

WORKDIR /app

# Layer 1: install dependencies (cached unless pyproject.toml/uv.lock changes)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --no-dev --frozen --no-install-project

# Layer 2: copy source and install project
COPY src/ ./src/
RUN uv sync --no-dev --frozen

ENV HF_ENDPOINT=https://hf-mirror.com \
    HF_HUB_DISABLE_SYMLINKS_WARNING=1 \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

EXPOSE 8000

ENTRYPOINT ["aether-mcp-server"]
CMD ["http", "--host", "0.0.0.0", "--port", "8000"]
