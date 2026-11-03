# syntax=docker/dockerfile:1
# Produkcyjny obraz galwork-hub (FastAPI + SpiffWorkflow).
# Baza danych NIE jest częścią tego obrazu - łączymy się do istniejącego
# PostgreSQL (zob. DATABASE_URL w .env / docker-compose.yml).

FROM python:3.12-slim AS builder

WORKDIR /app

# Zależności kompilacyjne tylko na czas `pip install` - część pakietów
# (lxml, cryptography) może wymagać ich przy braku gotowego wheela dla
# docelowej architektury. Finalny obraz ich nie zawiera (etap `runtime`).
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc libpq-dev libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


FROM python:3.12-slim AS runtime

# libpq5/libxml2/libxslt1.1 - biblioteki runtime (bez -dev) dla psycopg2/lxml.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 libxml2 libxslt1.1 curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 galwork

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY --chown=galwork:galwork . .

# config/config.ini jest w .dockerignore (ten na dysku ma sekrety). Obraz
# dostaje bezpieczny szablon - configparser wymaga, żeby plik i klucze
# istniały, żeby `_section_values()` (config/config.py) mogło je nadpisać
# zmiennymi środowiskowymi <SEKCJA>_<KLUCZ> albo DATABASE_URL (patrz .env).
RUN cp config/config.ini.example config/config.ini

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
#RUN chmod 777 /app
RUN chown -R galwork:galwork /app

USER galwork


EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]

# liczba workerów do doboru wg CPU hosta - nadpisywalne przez
# `command:` w docker-compose.yml (patrz UVICORN_WORKERS w .env).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
