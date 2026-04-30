FROM python:3.12-slim

LABEL org.opencontainers.image.title="OpenViking"
LABEL org.opencontainers.image.description="Context database for AI agents with semantic search, tiered summaries, and MCP server integration"
LABEL org.opencontainers.image.url="https://github.com/openviking/openviking"
LABEL org.opencontainers.image.source="https://github.com/openviking/openviking"
LABEL org.opencontainers.image.licenses="Apache-2.0"

# Create non-root user and application directories
RUN groupadd --system openviking && \
    useradd --system --gid openviking --no-create-home --shell /usr/sbin/nologin openviking && \
    mkdir -p /app /data /config && \
    chown -R openviking:openviking /app /data /config

WORKDIR /app

# Install uv for fast, reproducible dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install dependencies from lockfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code and entrypoint
COPY mcp-server.py ./
COPY docker-entrypoint.sh ./
RUN chmod +x /app/docker-entrypoint.sh

ENV PATH="/app/.venv/bin:$PATH"
ENV OPENVIKING_CONFIG_FILE=/config/ov.conf

EXPOSE 1940

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:1940/health')" || exit 1

USER openviking

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["openviking-server", "--host", "0.0.0.0", "--port", "1940"]
