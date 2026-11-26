FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH MCP_TRANSPORT=http PYTHONUNBUFFERED=1
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["k8s-readonly-mcp"]
