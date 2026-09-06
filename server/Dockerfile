# syntax=docker/dockerfile:1
# T-M2-01: Multi-stage build — secrets and build tools stay out of runtime image.
# R-M4-39: build context is the repo root (docker-compose ``build.context: .``)
# so that ``contracts/`` is reachable. All COPY paths are repo-root-relative.

# -- builder ---------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

COPY server/pyproject.toml ./
RUN pip install --no-cache-dir --prefix=/install .

COPY server/src/ ./src/

# -- runtime ---------------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL maintainer="aozora"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 1001 aozora \
    && useradd --system --uid 1001 --gid aozora aozora

WORKDIR /app

COPY --from=builder /install /usr/local

COPY server/src/ ./src/
# R-M4-39: validator.py resolves the schema via parents[4] / "contracts",
# so the directory must exist at /app/contracts inside the container.
COPY contracts/ ./contracts/

RUN chown -R aozora:aozora /app
USER aozora

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
