# syntax=docker/dockerfile:1

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=tfapp.settings

WORKDIR /app

# Pillow, lxml, and reportlab system libraries
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libjpeg62-turbo \
        libpng16-16 \
        libxml2 \
        libxslt1.1 \
        zlib1g \
    && rm -rf /var/lib/apt/lists/*

COPY tfapp/requirements.txt tfapp/requirements-prod.txt ./
RUN pip install --upgrade pip \
    && pip install -r requirements-prod.txt

COPY tfapp/ .

RUN chmod +x docker/entrypoint.sh

# Collect static assets at build time (no DB required).
ARG DJANGO_SECRET_KEY=build-time-only-not-for-production
ENV DJANGO_SECRET_KEY=${DJANGO_SECRET_KEY} \
    DJANGO_DEBUG=False \
    DJANGO_USE_WHITENOISE=1
RUN python manage.py collectstatic --noinput --clear

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "tfapp.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
