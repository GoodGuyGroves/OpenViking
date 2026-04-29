FROM python:3.12-slim

WORKDIR /app

# Install uv for fast, reproducible dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Install dependencies from lockfile
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY mcp-server.py ./

ENV PATH="/app/.venv/bin:$PATH"
ENV OPENVIKING_CONFIG_FILE=/config/ov.conf

EXPOSE 1940
CMD ["openviking-server", "--host", "0.0.0.0", "--port", "1940"]
