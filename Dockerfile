FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 \
    SENSELESS_DATA=/data SENSELESS_TOKEN_CACHE=/data/token.json SENSELESS_HTTP_PORT=8780

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY senseless ./senseless
RUN uv sync --frozen --no-dev

# Default 568:568 = TrueNAS "apps". Override with `user:` in compose.
USER 568:568
VOLUME /data
EXPOSE 8780
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8780/healthz', timeout=4).status == 200 else 1)"
CMD ["senseless", "collect"]
