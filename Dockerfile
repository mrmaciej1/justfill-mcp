# Glama exposes stdio MCP servers through mcp-proxy. Keep the proxy and the
# Python server in one image so its build does not depend on NodeSource, uv's
# Python installer, or a git clone performed inside the container.
FROM node:24-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/justfill-venv \
    PATH="/opt/justfill-venv/bin:${PATH}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv \
    && npm install --global mcp-proxy@6.4.3 \
    && python3 -m venv "${VIRTUAL_ENV}" \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 justfill \
    && chown -R justfill:justfill /app

USER justfill

CMD ["mcp-proxy", "--", "justfill-mcp"]
