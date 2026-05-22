# ============================================================
# Aurora LTS — Production Container
# ============================================================
# Two-stage build for a minimal, secure runtime image:
#   Stage 1 (builder)  : has the build toolchain + pre-builds wheels
#   Stage 2 (runtime)  : slim base + WeasyPrint system libs only
#
# Notes:
#   • Non-root user `aurora` runs the app
#   • No secrets baked into the image — every secret comes from the
#     Cloud Run runtime via Secret Manager (--set-secrets)
#   • PORT defaults to 8080 (Cloud Run convention)
#   • Listens on 0.0.0.0:$PORT
#
# Build:
#   docker build -t aurora-api:dev .
#
# Run (local smoke):
#   docker run --rm -p 8080:8080 \
#     -e JWT_SECRET=dev-secret \
#     -e SKIP_SEED_ADMIN=1 \
#     aurora-api:dev
# ============================================================


# ─────────────────────────────────────────────────────────────
# Stage 1 — builder
# ─────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder

WORKDIR /build

# Build-time tools needed to compile any wheel that doesn't ship a
# manylinux build (e.g. some cryptography variants on Python 3.14;
# also `jiter` / PyO3 Rust extensions used by the anthropic SDK).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libffi-dev \
        libssl-dev \
        libpq-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install Rust toolchain for native PyO3 wheels (anthropic → jiter → PyO3).
# rustup's default toolchain is sufficient; install to a shared path.
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal
ENV PATH="/root/.cargo/bin:${PATH}"

# PyO3 currently caps at Python 3.13. Setting this env var tells PyO3 to
# build against the stable ABI for forward-compatibility with Python 3.14.
# Required while we're on python:3.14-slim and depend on jiter (anthropic).
ENV PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1

# Use the locked dependency manifest. requirements.lock is what
# requirements.txt freezes to; using the lock guarantees byte-identical
# rebuilds across machines.
COPY requirements.lock /build/requirements.txt

# Pre-build wheels into a directory so the runtime stage installs from
# wheels only (faster, no compiler in the runtime image).
RUN pip install --upgrade pip && \
    pip wheel --no-cache-dir --wheel-dir /build/wheels -r requirements.txt


# ─────────────────────────────────────────────────────────────
# Stage 2 — runtime
# ─────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

# Default env. Overridden per-Cloud-Run-service via --set-env-vars.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    AURORA_RUNTIME=cloud_run \
    PORT=8080

# WeasyPrint runtime libs (Cairo / Pango / GDK-PixBuf) plus default
# fonts that cover Latin + Hebrew + Arabic glyph ranges. Without these
# fonts, PDFs render with tofu boxes for non-Latin characters.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        fonts-liberation \
        fonts-noto-core \
        fonts-noto-cjk \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create a dedicated non-root user. /app is the working dir; /tmp is
# Cloud Run's writable scratchpad (used for ephemeral PDFs + KYC stubs
# until the GCS migration in Sprint 2).
RUN groupadd --system aurora && \
    useradd --system --gid aurora --home /app --shell /usr/sbin/nologin aurora

WORKDIR /app

# Install dependencies from the wheels built in stage 1.
COPY --from=builder /build/wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Copy application code. server_files/ is the FastAPI app root —
# everything inside lives at /app/<...> in the container.
COPY server_files/ /app/

# Optional: ship a .well-known directory or default static assets.
# The dashboard and onboarding HTML files are already inside server_files/.

# Ensure /app is owned by the non-root user.
RUN chown -R aurora:aurora /app

# Cloud Run sets PORT — listen on it. Health checks hit / and
# /api/v1/onboarding/health (200 OK in <2s after container boot).
EXPOSE 8080

# Drop privileges before the process starts.
USER aurora

# Health check (used by docker compose / k8s; Cloud Run uses its own).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT}/api/v1/onboarding/health || exit 1

# Production process manager:
#   - gunicorn forks N worker processes managed by UvicornWorker
#   - 2 workers × 80 concurrency = 160 in-flight requests/instance
#   - --timeout 120s matches Cloud Run's per-request timeout
#   - --graceful-timeout 30s gives SIGTERM handlers time to drain
CMD ["sh", "-c", "exec gunicorn \
    -k uvicorn.workers.UvicornWorker \
    -w 2 \
    -b 0.0.0.0:${PORT} \
    --timeout 120 \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    app.main:app"]
