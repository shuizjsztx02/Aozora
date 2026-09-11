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
# 生产部署（docker-compose.prod.yml / a-deploy.sh）在容器内执行
# ``alembic upgrade head``：CLI 需要 alembic.ini（script_location=src/migrations
# 相对 WORKDIR /app 解析，迁移脚本已随 src 拷入）。
COPY server/alembic.ini ./alembic.ini
# R-M4-39: validator.py resolves the schema via parents[4] / "contracts".
# With WORKDIR /app, parents[4] == "/", so contracts must live at /contracts
# (absolute path). Using ./contracts/ would place them at /app/contracts and
# cause FileNotFoundError on the first /ai/validate call.
COPY contracts/ /contracts/

# 附件/导出持久卷挂载点（compose volumes: <name>:/data）。空目录随镜像
# 建立并 chown——命名卷首次挂载时从镜像继承属主，非 root 用户才可写。
RUN mkdir -p /data/uploads /data/exports

RUN chown -R aozora:aozora /app /data
USER aozora

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
