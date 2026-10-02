# Bonds Deal Slip Parser - API image (stateless: slips are never stored, ADR-0009)
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    BONDS_DATA_DIR=/srv/bonds/data \
    BONDS_TEMPLATES_DIR=/srv/bonds/templates

WORKDIR /srv/bonds

# Runtime dependencies only (reportlab is for the mock generator, pytest/httpx for tests).
COPY requirements.txt .
RUN grep -vE '^(reportlab|pytest|httpx)' requirements.txt > /tmp/runtime.txt \
    && pip install -r /tmp/runtime.txt \
    && rm /tmp/runtime.txt

COPY app ./app

# Non-root user; only the template and data folders are writable.
RUN useradd --create-home --uid 10001 bonds \
    && mkdir -p /srv/bonds/data /srv/bonds/templates \
    && chown -R bonds:bonds /srv/bonds/data /srv/bonds/templates
USER bonds

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

# One worker: the template / client / audit files expect a single writer (ADR-0009).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
