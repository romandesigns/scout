FROM oven/bun:1.3.14 AS web-build
WORKDIR /web
COPY VERSION /VERSION
COPY web/package.json ./package.json
RUN bun install
COPY web ./
ENV NEXT_PUBLIC_SCOUT_SAME_ORIGIN=1
ENV NEXT_TELEMETRY_DISABLED=1
RUN bun run build

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONPATH=/srv \
    WEB_OUT_DIR=/srv/web-out

WORKDIR /srv

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --disable-pip-version-check --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY --from=web-build /web/out ./web-out

RUN mkdir -p /data /charts

CMD ["python", "-m", "app.main"]
