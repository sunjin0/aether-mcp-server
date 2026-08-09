FROM python:3.11-slim

# Curated platform runtime: Skill scripts consume these libraries but never
# install packages or select a base image themselves.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libcairo2 libgdk-pixbuf-2.0-0 libffi8 \
    fonts-noto-cjk fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    # WeasyPrint 62 uses the pydyf stream API that was removed in pydyf 0.12.
    # Keep this platform runtime reproducible so a Skill's frozen template has
    # the same rendering behavior in every sandbox execution.
    && pip install --no-cache-dir weasyprint==62.3 pydyf==0.10.0 python-docx==1.1.2 openpyxl==3.1.5 pybars3==0.9.7
RUN useradd --system --uid 10001 sandbox
USER 10001
