FROM python:3.12-slim

# Set the environment variable to ensure Python outputs are not buffered
ENV PYTHONUNBUFFERED=1

# 1. supervisor (process manager)
RUN pip install --no-cache-dir supervisor

# 2. Fluent Bit
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://packages.fluentbit.io/fluentbit.key | gpg --dearmor -o /usr/share/keyrings/fluentbit.gpg \
 && echo "deb [signed-by=/usr/share/keyrings/fluentbit.gpg] https://packages.fluentbit.io/debian/bookworm bookworm main" > /etc/apt/sources.list.d/fluentbit.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends fluent-bit=5.0.8 \
 && rm -rf /var/lib/apt/lists/*

# 3. producers and configs
COPY supervisord.conf  /etc/supervisord.conf
COPY fluent-bit/parsers.yaml  /etc/fluent-bit/parsers.yaml
COPY fluent-bit/collector.yaml  /etc/fluent-bit/collector.yaml
COPY producers/        /app/producers/


# 4. where the file-tail producers write
RUN mkdir -p /var/log/node

# 5. PID 1 is the supervisor; it launches the 4 processes
CMD ["supervisord", "-c", "/etc/supervisord.conf"]