FROM python:3.13.16-slim

# supercronic: cron built for containers (keeps env vars, logs to stdout).
# The binary is verified against the SHA-256 published on its GitHub release.
ARG SUPERCRONIC_VERSION=v0.2.49
ARG SUPERCRONIC_SHA256=a53ae236602c7338aba3fbaff40bda6300eae3b9fedb8261eb06cfe3724430c1
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64 /usr/local/bin/supercronic
RUN echo "${SUPERCRONIC_SHA256}  /usr/local/bin/supercronic" | sha256sum -c - \
 && chmod 755 /usr/local/bin/supercronic

ENV TZ=America/Sao_Paulo \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY pipeline/ pipeline/
COPY sql/ sql/
COPY docker/ docker/

# Run as an unprivileged user.
RUN useradd --create-home app \
 && mkdir -p data logs reports \
 && chown -R app:app /app \
 && chmod +x docker/entrypoint.sh
USER app

HEALTHCHECK --interval=5m --timeout=10s --start-period=10m \
  CMD python -m pipeline.health || exit 1

ENTRYPOINT ["/app/docker/entrypoint.sh"]
