#!/usr/bin/env bash
# a-deploy.sh — 服务器 A（业务服务器，内网 192.168.44.13）上执行。
#
# 用法：
#   ./infra/deploy/prod/a-deploy.sh test     # 部署/更新测试实例（api-test, :18080）——默认
#   ./infra/deploy/prod/a-deploy.sh prod     # 部署/更新生产实例（api-prod, :8080）
#
# 步骤：前置检查（docker/.env/内网连通）→ 构建并起容器 → alembic upgrade head
#       → /health/ready 探活。幂等：重复执行即"重建+迁移+探活"的更新流程。
# 首次部署请先按 RUNBOOK 第 3 节准备仓库与 .env。
set -euo pipefail

PROFILE="${1:-test}"
case "$PROFILE" in
  test) SERVICE=api-test; HOST_PORT=18080; DB_PORT=15432; URL_VAR=TEST_DATABASE_URL ;;
  prod) SERVICE=api-prod; HOST_PORT=8080;  DB_PORT=5432;  URL_VAR=PROD_DATABASE_URL ;;
  *)    echo "用法: $0 [test|prod]" >&2; exit 2 ;;
esac

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

log()  { echo "[INFO]  $*"; }
fail() { echo "[FAIL]  $*" >&2; exit 1; }

# ── 前置检查 ────────────────────────────────────────────────────────────────
command -v docker >/dev/null || fail "docker 未安装（RUNBOOK 第 3.1 节）"
docker compose version >/dev/null 2>&1 || fail "docker compose V2 不可用（RUNBOOK 第 3.1 节）"
docker info >/dev/null 2>&1 || fail "docker daemon 未运行：sudo systemctl start docker && sudo systemctl enable docker"
[ -f .env ] || fail "缺少 $ROOT/.env —— cp infra/deploy/prod/deploy.env.example .env 并填写（RUNBOOK 第 3.3 节）"

# 该环境必填项（prod 还要求 CORS 与 AI key——ENV=production 启动校验器同样强制）
case "$PROFILE" in
  test) REQUIRED_VARS=(TEST_DATABASE_URL TEST_SECRET_KEY) ;;
  prod) REQUIRED_VARS=(PROD_DATABASE_URL PROD_SECRET_KEY PROD_CORS_ORIGINS AI_PROVIDER_API_KEY) ;;
esac
for v in "${REQUIRED_VARS[@]}"; do
  grep -Eq "^${v}=[^<[:space:]].*" .env || fail ".env 中 ${v} 未填写或仍是占位符"
done

DB_HOST="$(grep -E "^${URL_VAR}=" .env | sed -E 's|.*://[^@]*@([^:/]+):([0-9]+).*|\1|')"
DB_PORT_ACTUAL="$(grep -E "^${URL_VAR}=" .env | sed -E 's|.*://[^@]*@([^:/]+):([0-9]+).*|\2|')"
if [ "$DB_PORT_ACTUAL" != "$DB_PORT" ]; then
  log "提示: ${URL_VAR} 的端口为 ${DB_PORT_ACTUAL}（架构图规划值 ${DB_PORT}），按实际值连通性检查"
fi

log "检查服务器 B 数据库内网连通（${DB_HOST}:${DB_PORT_ACTUAL}）..."
if ! timeout 5 bash -c "cat < /dev/null > /dev/tcp/${DB_HOST}/${DB_PORT_ACTUAL}" 2>/dev/null; then
  fail "A→B 内网不通。排查：① 云安全组是否放行 B 机 ${DB_PORT_ACTUAL} ← 192.168.44.13；② B 机 listen_addresses；③ B 机本机防火墙"
fi
log "内网连通 ✓"

# ── 构建与启动 ──────────────────────────────────────────────────────────────
log "构建并启动 ${SERVICE}（首次构建约 1-3 分钟）..."
docker compose -f docker-compose.prod.yml --profile "$PROFILE" up -d --build

# ── 数据库迁移 ──────────────────────────────────────────────────────────────
log "执行 alembic upgrade head ..."
docker compose -f docker-compose.prod.yml --profile "$PROFILE" \
  run --rm "$SERVICE" alembic upgrade head

# ── 探活 ────────────────────────────────────────────────────────────────────
log "等待 ${SERVICE} 就绪（最多 60s）..."
ready=""
for _ in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:${HOST_PORT}/api/v1/health/ready" >/dev/null 2>&1; then
    ready=1; break
  fi
  sleep 2
done
if [ -z "$ready" ]; then
  fail "探活失败。查看日志: docker compose -f docker-compose.prod.yml --profile ${PROFILE} logs --tail 50 ${SERVICE}"
fi

cat <<EOF
════════════════════════════════════════════════════════════
✓ ${SERVICE} 部署完成，监听 127.0.0.1:${HOST_PORT}，/health/ready 通过
  查看状态: docker compose -f docker-compose.prod.yml --profile ${PROFILE} ps
  查看日志: docker compose -f docker-compose.prod.yml --profile ${PROFILE} logs -f ${SERVICE}
EOF
if [ "$PROFILE" = "test" ]; then
  log "下一步：确认 nginx 测试入口就绪并做公网验证（RUNBOOK 第 5、6 节）"
fi
