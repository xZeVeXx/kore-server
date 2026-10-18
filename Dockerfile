# Digest of the multi-arch index for 3.12-slim (checked 2026-10-10); Dependabot (docker) bumps tag and digest.
FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

WORKDIR /app

# Every package, including transitive ones, must match a hash from the lock file.
COPY server/requirements.lock ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY server/ ./
# A checkout made under `umask 077` copies owner-only files; the service user must still read the code.
RUN chmod -R a+rX /app

# The database lives in a volume, never in the image.
ENV KORE_DB_PATH=/data/kore.db
RUN mkdir -p /data && useradd --uid 10001 --no-create-home kore && chown kore /data
USER kore

# ingest_api.py validates where it listens before it starts uvicorn. Without Compose it listens on
# 127.0.0.1 inside the container; Compose passes the listen and published addresses it needs.
CMD ["python", "ingest_api.py"]
