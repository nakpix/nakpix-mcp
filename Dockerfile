FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NAKPIX_HOME=/nakpix

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir -e .

# Run as root so the container can read/write the bind-mounted ~/.nakpix
# (the agent's local wallet + schedule DB) regardless of host ownership.

# Default: the MCP server (stdio). Compose overrides `command` for
# scheduler/incidents (a CMD is overridable; an ENTRYPOINT is not).
CMD ["nakpix-mcp"]
