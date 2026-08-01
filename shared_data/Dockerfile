FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates netcdf-bin \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY manifest.toml sync_shared_data.py ./

ENV SHARED_DATA_ROOT=/app/data
ENTRYPOINT ["python", "/app/sync_shared_data.py"]
