# TopSkill API image (Cloud Run service topskill-api).
# Build context = backend/ ; this file lives outside the context and is passed with -f:
#   docker build -f deploy/backend.Dockerfile -t topskill-api backend
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# 1) Dependencies only (cached layer; changes only when the lock file changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 2) Application code. The package is imported from /app (PYTHONPATH), so the project itself
#    does not need to be built or installed into the venv.
COPY topskill ./topskill

RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin topskill \
    && chown -R topskill /app
USER topskill

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app \
    PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "exec uvicorn topskill.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*' --timeout-keep-alive 75"]
