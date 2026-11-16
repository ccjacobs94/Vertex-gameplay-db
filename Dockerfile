FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app \
    VERTEX_DATA_DIR=/app/data \
    VERTEX_BIND_HOST=0.0.0.0 \
    VERTEX_PORT=8090 \
    VERTEX_MCP_BIND_HOST=0.0.0.0 \
    VERTEX_MCP_PORT=8096

RUN apt-get update && apt-get install -y --no-install-recommends gosu curl sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.lock

COPY . .
RUN chmod +x /app/entrypoint.sh \
    && printf '#!/bin/sh\nexec python -m vertex "$@"\n' > /usr/local/bin/vertex \
    && chmod +x /usr/local/bin/vertex \
    && python -m vertex build-frontend --out-dir /app/frontend/dist

EXPOSE 8090 8096

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8090/readyz || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["sh", "-c", "python -m vertex migrate && exec python -m vertex serve"]
