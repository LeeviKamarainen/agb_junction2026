FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.26 /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_DEV=1

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project

COPY app ./app

EXPOSE 8000
CMD ["/app/.venv/bin/fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
