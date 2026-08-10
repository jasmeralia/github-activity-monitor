# syntax=docker/dockerfile:1
FROM python:3.14-slim AS builder

WORKDIR /build

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install .

FROM python:3.14-slim AS final

RUN groupadd --gid 1001 monitor && \
    useradd --uid 1001 --gid monitor --shell /bin/sh --no-create-home monitor && \
    mkdir -p /data && chown monitor:monitor /data

COPY --from=builder /install /usr/local

USER monitor

VOLUME ["/data"]

ENTRYPOINT ["python", "-m", "git_activity_monitor"]
