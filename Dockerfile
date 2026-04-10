# ── Stage 1: builder ──────────────────────────────────────────────────────
# Installs dependencies into a virtual env using the locked versions.
# The uv binary and all build-time tooling stay in this stage only.
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy only the files needed to resolve and install dependencies first,
# so this layer is cached as long as the lockfile doesn't change.
COPY pyproject.toml uv.lock ./

# Install production dependencies into an in-project venv, strictly from
# the lockfile (--frozen) and without dev extras (--no-dev).
RUN uv sync --frozen --no-dev --no-install-project

# Now copy the source and install the project itself.
COPY README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev

# ── Stage 2: runtime ──────────────────────────────────────────────────────
# Lean final image: only Python, the venv, and the application source.
# No uv, no build tools, no cache.
FROM python:3.14-slim AS runtime

WORKDIR /app

# Copy the populated venv and source from the builder stage.
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Put the venv on PATH so `python` resolves to the venv interpreter.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

COPY departic.example.yaml ./

RUN mkdir -p /data

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/health')"

CMD ["python", "-m", "uvicorn", "departic.main:app", \
     "--host", "0.0.0.0", "--port", "8080"]
