FROM oven/bun:1.3.14 AS web-build
WORKDIR /web
COPY VERSION /VERSION
COPY web/package.json ./package.json
RUN bun install
COPY web ./
ENV NEXT_PUBLIC_SCOUT_SAME_ORIGIN=1
ENV NEXT_TELEMETRY_DISABLED=1
RUN bun run build

# Build the exact Rust perception core that was validated by the replay suite.
# The same binary supports both historical replay and the live JSONL stream.
FROM rust:1.88-slim-bookworm AS rust-build
WORKDIR /rust/market-replay
COPY rust/market-replay/Cargo.toml rust/market-replay/Cargo.lock ./
COPY rust/market-replay/src ./src
RUN cargo build --release --locked

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore \
    MPLCONFIGDIR=/tmp/matplotlib \
    PYTHONPATH=/srv \
    WEB_OUT_DIR=/srv/web-out \
    RUST_PERCEPTION_BINARY=/usr/local/bin/scout-market-replay

WORKDIR /srv

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
COPY VERSION ./VERSION
RUN pip install --disable-pip-version-check --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY --from=web-build /web/out ./web-out
COPY --from=rust-build /rust/market-replay/target/release/scout-market-replay /usr/local/bin/scout-market-replay

RUN mkdir -p /data /charts

CMD ["python", "-m", "app.main"]
