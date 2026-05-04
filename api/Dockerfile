FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip \
 && pip install .

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
# `--proxy-headers --forwarded-allow-ips=*` makes uvicorn honour the
# X-Forwarded-Proto / X-Forwarded-For / X-Forwarded-Host headers that the
# bundled nginx (and any outer reverse proxy) sets, so request.url.scheme,
# request.client.host and OpenAPI docs reflect the public URL instead of
# `http://api:8000`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
