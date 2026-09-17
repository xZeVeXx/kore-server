FROM python:3.12-slim

WORKDIR /app

COPY server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY server/ ./

# The database lives in a volume, never in the image.
ENV KORE_DB_PATH=/data/kore.db
RUN mkdir -p /data && useradd --uid 10001 --no-create-home kore && chown kore /data
USER kore

CMD ["uvicorn", "ingest_api:app", "--host", "0.0.0.0", "--port", "8081"]
