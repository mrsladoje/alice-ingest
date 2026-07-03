# Flight-2 replay engine: streams real CERN logs from s3://epn-backup-logs/
# back through the existing node collector. Kept deliberately small — the only
# dependency beyond stdlib is boto3 (for Ceph RGW / S3).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir boto3

WORKDIR /app
COPY replay.py /app/replay.py

# Default: run the HTTP trigger stub and idle until a POST /replay (or a button)
# kicks off a run. Override with `... run --family infologger,dds` for one-shot.
CMD ["python", "replay.py", "serve"]
