FROM python:3.14-slim
ARG VERSION=dev
LABEL org.opencontainers.image.title="AuditDesk" org.opencontainers.image.version=$VERSION
LABEL org.opencontainers.image.licenses="MIT"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATABASE_PATH=/app/data/audit.sqlite3 APP_HOST=0.0.0.0 APP_PORT=8765 AUTH_MODE=cloudflare EMAIL_ENABLED=false SCHEDULER_ENABLED=false
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock && useradd --uid 10001 --create-home auditdesk && mkdir /app/data && chown auditdesk:auditdesk /app/data
COPY audit_app ./audit_app
COPY main.py bridge_fields.json LICENSE ./
USER auditdesk
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/healthz',timeout=3)"
CMD ["gunicorn", "--bind", "0.0.0.0:8765", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "--access-logformat", "%(m)s %(U)s %(s)s", "audit_app.wsgi:app"]
