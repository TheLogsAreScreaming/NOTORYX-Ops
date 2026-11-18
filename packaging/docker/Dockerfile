# NOTORYX Ops station. Build from the release folder that holds the wheel:
#   docker compose up -d
FROM python:3.12-slim
RUN useradd --create-home --uid 1000 ops && mkdir -p /data && chown ops /data
COPY notoryx_ops-*.whl /tmp/
RUN pip install --no-cache-dir /tmp/notoryx_ops-*.whl && rm /tmp/*.whl
USER ops
ENV NOTORYX_OPS_CONFIG=/data/config.toml
EXPOSE 8484
CMD ["notoryx-ops", "run", "--host", "0.0.0.0"]
