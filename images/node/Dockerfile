FROM python:3.12-slim

# Set the environment variable to ensure Python outputs are not buffered
ENV PYTHONUNBUFFERED=1

# 1. supervisor (process manager)
RUN pip install --no-cache-dir supervisor

# 2. Fluent Bit
# Pinned to 4.0.14, and it must stay on 4.x. 5.0.8 loses bytes appended to a
# file after logrotate renames it away — docs/SOAK_RESULTS.md round 10. The
# collector role refuses to install 5.x for the same reason; this file is the
# other place the version is chosen and the two must not drift.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://packages.fluentbit.io/fluentbit.key | gpg --dearmor -o /usr/share/keyrings/fluentbit.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/fluentbit.gpg] https://packages.fluentbit.io/debian/bookworm bookworm main" > /etc/apt/sources.list.d/fluentbit.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends fluent-bit=4.0.14 \
 && rm -rf /var/lib/apt/lists/*

# 3. producers and configs
COPY supervisord-self-contained.conf            /etc/supervisord-self-contained.conf
COPY supervisord-collector.conf  /etc/supervisord-collector.conf
COPY fluent-bit/parsers.yaml  /etc/fluent-bit/parsers.yaml
COPY fluent-bit/collector.yaml  /etc/fluent-bit/collector.yaml
COPY producers/        /app/producers/


# 4. log files go in /var/log/node; Fluent Bit keeps its offsets in flb-storage
RUN mkdir -p /var/log/node /var/log/flb-storage

# 5. PID 1 is the supervisor; it launches the 4 processes
CMD ["supervisord", "-c", "/etc/supervisord-collector.conf"]