FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates git ripgrep && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY benchmark/__init__.py benchmark/__main__.py benchmark/cli.py ./benchmark/
COPY benchmark/participant ./benchmark/participant
COPY data/benchmark_release ./data/benchmark_release

ENTRYPOINT ["python", "-m", "benchmark"]
