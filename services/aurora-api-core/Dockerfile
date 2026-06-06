# ============================================================
# Aurora LTS — M2 Operational Core API  (aurora-api-core)
# ============================================================
# Monorepo layout (build context = REPOSITORY ROOT):
#   shared_packages/aurora_shared   -> built to a wheel, pip-installed
#   services/aurora-api-core/app    -> the M2 FastAPI package (app.main_core:app)
#
# Entrypoint app.main_core:app mounts ONLY copilot + native_shell. No front-end.
# Runtime apt mirrors M1 for now (the locked wheels include weasyprint etc. that
# M2 never imports — slimming M2's deps is a tracked follow-up optimization).
#
# Build (from repo root):
#   docker build -f services/aurora-api-core/Dockerfile -t aurora-api-core:dev .
# ============================================================

# ── Stage 1 — builder ──
FROM python:3.14-slim AS builder
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libffi-dev libssl-dev libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"
ENV PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1

COPY requirements.lock /build/requirements.txt
RUN pip install --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt

COPY shared_packages/aurora_shared /build/aurora_shared
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /build/wheels /build/aurora_shared


# ── Stage 2 — runtime ──
FROM python:3.14-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AURORA_RUNTIME=cloud_run \
    PORT=8080

RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 libffi8 \
        fonts-liberation fonts-noto-core fonts-noto-cjk curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system aurora && \
    useradd --system --gid aurora --home /app --shell /usr/sbin/nologin aurora

WORKDIR /app

COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY services/aurora-api-core/app /app/app

RUN chown -R aurora:aurora /app
EXPOSE 8080
USER aurora

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT}/ || exit 1

CMD ["sh", "-c", "exec gunicorn \
    -k uvicorn.workers.UvicornWorker \
    -w 2 \
    -b 0.0.0.0:${PORT} \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    app.main_core:app"]
