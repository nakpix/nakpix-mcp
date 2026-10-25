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

RUN useradd -m -u 1000 nakpix && mkdir -p /nakpix && chown -R nakpix:nakpix /nakpix
USER nakpix

# Default: the MCP server (stdio). Compose overrides `command` for
# scheduler/incidents (a CMD is overridable; an ENTRYPOINT is not).
CMD ["nakpix-mcp"]
