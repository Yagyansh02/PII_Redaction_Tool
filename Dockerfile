FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_RETRIES=10 \
    DEBIAN_FRONTEND=noninteractive \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    PORT=10000

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
        tesseract-ocr \
        zbar-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-cloud.txt ./
RUN pip install --no-cache-dir -r requirements-cloud.txt

COPY pii_redactor ./pii_redactor
COPY web ./web

RUN useradd --create-home --uid 10001 redactor \
    && chown -R redactor:redactor /app
USER redactor

EXPOSE 10000
CMD ["python", "-m", "web"]
