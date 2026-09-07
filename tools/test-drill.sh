#!/usr/bin/env bash
# tools/test-drill.sh — T-M5-03b 隔离恢复演练
#
# 在 docker-compose 隔离环境中执行完整的 备份 → 恢复 → 校验 流程，
# 记录 RPO / RTO 实测值并输出演练记录。
#
# 使用 docker-compose.drill.yml（postgres 18 专用存储路径）。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

DRILL_COMPOSE="docker-compose.drill.yml"
DRILL_SERVICE="drill-db"
BACKUP_DIR_HOST="${PROJECT_ROOT}/.drill-backups"
DRILL_DB="aozora_drill_restore"
PG_IMAGE="postgres:18-alpine"
DRILL_PORT=15432
COMPOSE_PROJECT="aozoradrill"

# ── 辅助函数 ──
dc() {
  docker compose -p "${COMPOSE_PROJECT}" -f "${DRILL_COMPOSE}" "$@"
}

psql_in_db() {
  local db="$1"; shift
  dc exec -T "${DRILL_SERVICE}" \
    psql --username=aozora --dbname="${db}" --no-align --tuples-only "$@"
}

pg_in_container() {
  MSYS_NO_PATHCONV=1 docker run --rm \
    --network="${COMPOSE_PROJECT}_default" \
    -e PGPASSWORD=aozora \
    -v "${BACKUP_DIR_HOST}:/backups" \
    "${PG_IMAGE}" "$@"
}

# ── 清理函数 ──
cleanup_drill() {
  echo ""
  echo "[drill] Cleaning up..."
  pg_in_container dropdb --host=drill-db --port=5432 --username=aozora \
    --if-exists "${DRILL_DB}" 2>/dev/null || true
  dc down -v --remove-orphans 2>/dev/null || true
  rm -rf "${BACKUP_DIR_HOST}"
  echo "[drill] Cleanup done."
}
trap cleanup_drill EXIT

echo "╔══════════════════════════════════════════╗"
echo "║  T-M5-03b 隔离恢复演练                  ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── Step 0: 准备 ──
START_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
START_EPOCH=$(date +%s)
echo "[drill] Step 0: Preparing (start_time: ${START_TIME})"

rm -rf "${BACKUP_DIR_HOST}"
mkdir -p "${BACKUP_DIR_HOST}"

# ── Step 1: 启动数据库 ──
echo "[drill] Step 1: Starting database"
dc up -d "${DRILL_SERVICE}"
echo "[drill] Waiting for db to be healthy..."
for i in $(seq 1 45); do
  if dc exec -T "${DRILL_SERVICE}" pg_isready -U aozora &>/dev/null; then
    echo "[drill]   db is ready (after ${i}s)"
    break
  fi
  if [[ $i -eq 45 ]]; then
    echo "[drill] ERROR: db did not become ready in 45s" >&2
    dc logs "${DRILL_SERVICE}" 2>&1 | tail -10
    exit 1
  fi
  sleep 1
done

# ── Step 2: 运行 Alembic 迁移 ──
echo "[drill] Step 2: Running Alembic migrations"
cd "${PROJECT_ROOT}/server"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8 LC_ALL=C.UTF-8 LANG=C.UTF-8
export DATABASE_URL="postgresql+asyncpg://aozora:aozora@localhost:${DRILL_PORT}/aozora"
python -X utf8 -m alembic -c alembic.ini upgrade head 2>&1 | tail -5
ALEMBIC_EXIT=$?
if [[ $ALEMBIC_EXIT -ne 0 ]]; then echo "[drill] WARNING: Alembic exited with code ${ALEMBIC_EXIT}" >&2; fi
cd "${PROJECT_ROOT}"

# ── Step 3: 导入种子数据 ──
echo "[drill] Step 3: Seeding fixture data (basic scenario)"
cd "${PROJECT_ROOT}/server"
export DATABASE_URL="postgresql+asyncpg://aozora:aozora@localhost:${DRILL_PORT}/aozora"
python -X utf8 "${PROJECT_ROOT}/tools/drill_seed.py" 2>&1 || echo "[drill] WARNING: Fixture seeding failed" >&2
cd "${PROJECT_ROOT}"

SEED_EPOCH=$(date +%s)
echo "[drill]   Seed completed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── Step 4: 执行备份 ──
echo "[drill] Step 4: Running backup (via docker container)"
BACKUP_TS=$(date -u +%Y%m%d-%H%M%S)
DUMP_FILE="aozora-${BACKUP_TS}.dump"

MSYS_NO_PATHCONV=1 docker run --rm \
  --network="${COMPOSE_PROJECT}_default" \
  -e PGPASSWORD=aozora \
  -v "${BACKUP_DIR_HOST}:/backups" \
  "${PG_IMAGE}" \
  pg_dump \
    --host=drill-db --port=5432 --username=aozora --dbname=aozora \
    --format=custom --compress=6 \
    --no-owner --no-privileges \
    --exclude-table-data='ai_usage' \
    --file="/backups/${DUMP_FILE}"

BACKUP_EPOCH=$(date +%s)
echo "[drill]   Backup completed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 验证备份文件
DUMP_SIZE=$(stat -c%s "${BACKUP_DIR_HOST}/${DUMP_FILE}" 2>/dev/null || stat -f%z "${BACKUP_DIR_HOST}/${DUMP_FILE}" 2>/dev/null || echo "?")
echo "[drill]   Backup file: ${DUMP_FILE} (${DUMP_SIZE} bytes)"

# 计算 SHA-256
if command -v sha256sum &>/dev/null; then
  (cd "${BACKUP_DIR_HOST}" && sha256sum "${DUMP_FILE}" > "${DUMP_FILE}.sha256")
elif command -v shasum &>/dev/null; then
  (cd "${BACKUP_DIR_HOST}" && shasum -a 256 "${DUMP_FILE}" > "${DUMP_FILE}.sha256")
fi
echo "[drill]   Checksum: $(cat "${BACKUP_DIR_HOST}/${DUMP_FILE}.sha256" 2>/dev/null || echo "N/A")"

# pg_restore --list 验证表
TABLE_LIST=$(pg_in_container pg_restore --list "/backups/${DUMP_FILE}" 2>/dev/null | grep -ci "TABLE" || echo "0")
echo "[drill]   Tables in backup: ${TABLE_LIST}"

echo "[drill]   Table names:"
pg_in_container pg_restore --list "/backups/${DUMP_FILE}" 2>/dev/null \
  | grep -i "TABLE" | sed 's/.*TABLE //' | sort | while read -r t; do
  echo "[drill]     - ${t}"
done

# ── Step 5: 恢复至临时数据库 ──
RESTORE_START=$(date +%s)
RESTORE_START_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
echo "[drill] Step 5: Restoring to '${DRILL_DB}' (start: ${RESTORE_START_TIME})"

pg_in_container createdb --host=drill-db --port=5432 --username=aozora "${DRILL_DB}"

# 使用临时文件捕获 pg_restore 退出码，避免管道 + `|| true` 吞掉真实失败（H5 修复）
RESTORE_LOG="${BACKUP_DIR_HOST}/pg_restore.log"
set +e
pg_in_container pg_restore \
  --host=drill-db --port=5432 --username=aozora \
  --dbname="${DRILL_DB}" \
  --clean --if-exists \
  --no-owner --no-privileges \
  "/backups/${DUMP_FILE}" > "${RESTORE_LOG}" 2>&1
RESTORE_EXIT=$?
set -e
echo "[drill]   pg_restore exit code: ${RESTORE_EXIT}"
# 仅展示末尾若干行，避免刷屏
tail -5 "${RESTORE_LOG}" 2>/dev/null || true
if [[ ${RESTORE_EXIT} -ne 0 ]]; then
  echo "[drill] WARNING: pg_restore reported errors (exit ${RESTORE_EXIT})" >&2
fi

# ── Step 6: 运行 Alembic 迁移到恢复库 ──
echo "[drill] Step 6: Running Alembic on restored database"
cd "${PROJECT_ROOT}/server"
export DATABASE_URL="postgresql+asyncpg://aozora:aozora@localhost:${DRILL_PORT}/${DRILL_DB}"
python -X utf8 -m alembic -c alembic.ini upgrade head 2>&1 | tail -3 || true
cd "${PROJECT_ROOT}"

# ── Step 7: 校验恢复数据 ──
echo "[drill] Step 7: Verifying restored data"

RESTORED_TABLES=$(psql_in_db "${DRILL_DB}" -c "
  SELECT COUNT(*) FROM information_schema.tables
  WHERE table_schema='public' AND table_type='BASE TABLE';
" 2>/dev/null | tr -d '[:space:]')

RESTORED_TXS=$(psql_in_db "${DRILL_DB}" -c "SELECT COUNT(*) FROM transactions;" 2>/dev/null | tr -d '[:space:]')
RESTORED_USERS=$(psql_in_db "${DRILL_DB}" -c "SELECT COUNT(*) FROM users;" 2>/dev/null | tr -d '[:space:]')
RESTORED_CATS=$(psql_in_db "${DRILL_DB}" -c "SELECT COUNT(*) FROM categories;" 2>/dev/null | tr -d '[:space:]')
RESTORED_BOOKS=$(psql_in_db "${DRILL_DB}" -c "SELECT COUNT(*) FROM books;" 2>/dev/null | tr -d '[:space:]')
RESTORED_ACCTS=$(psql_in_db "${DRILL_DB}" -c "SELECT COUNT(*) FROM accounts;" 2>/dev/null | tr -d '[:space:]')

# 校验 checksum
CHECKSUM_OK="false"
if [[ -f "${BACKUP_DIR_HOST}/${DUMP_FILE}.sha256" ]]; then
  if MSYS_NO_PATHCONV=1 docker run --rm -v "${BACKUP_DIR_HOST}:/backups" "${PG_IMAGE}" \
    sh -c "cd /backups && sha256sum -c ${DUMP_FILE}.sha256" 2>&1 | grep -qi "ok"; then
    CHECKSUM_OK="true"
  fi
fi

# ── Step 8: 计算指标 ──
END_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)
END_EPOCH=$(date +%s)
RESTORE_DURATION=$((END_EPOCH - RESTORE_START))
TOTAL_DURATION=$((END_EPOCH - START_EPOCH))
# RPO_MEASURED: 自本次演练备份完成到演练结束的时间间隔（= 演练"年龄"）。
# 语义上是"本次演练可恢复数据的最老年龄"，非跨演练的 RPO（L4 修复：重命名更明确）。
DATA_LATENCY_AT_RESTORE=$((END_EPOCH - BACKUP_EPOCH))

format_duration() {
  local secs=$1
  if [[ $secs -ge 3600 ]]; then echo "$((secs/3600))h$(((secs%3600)/60))m"
  elif [[ $secs -ge 60 ]]; then echo "$((secs/60))m$((secs%60))s"
  else echo "${secs}s"; fi
}

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  演练结果                                ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  start_time:         ${START_TIME}"
echo "  end_time:           ${END_TIME}"
echo "  restore_duration:   ${RESTORE_DURATION}s ($(format_duration ${RESTORE_DURATION}))"
echo "  total_duration:     ${TOTAL_DURATION}s ($(format_duration ${TOTAL_DURATION}))"
echo "  data_latency:       ${DATA_LATENCY_AT_RESTORE}s (演练结束时备份的年龄)"
echo "    ≤ 86400s (24h):   $([ ${DATA_LATENCY_AT_RESTORE} -le 86400 ] && echo ✅ PASS || echo ❌ FAIL)"
echo "  rto_measured:       ${TOTAL_DURATION}s"
echo "    ≤ 14400s (4h):    $([ ${TOTAL_DURATION} -le 14400 ] && echo ✅ PASS || echo ❌ FAIL)"
echo "  checksum_match:     ${CHECKSUM_OK}"
echo "  pg_restore_exit:    ${RESTORE_EXIT}"
echo "  table_count:        ${RESTORED_TABLES} (expected ≥ 14)"
echo "  users:              ${RESTORED_USERS}"
echo "  books:              ${RESTORED_BOOKS}"
echo "  categories:         ${RESTORED_CATS}"
echo "  accounts:           ${RESTORED_ACCTS}"
echo "  transactions:       ${RESTORED_TXS}"
echo "  tables_in_backup:   ${TABLE_LIST}"
echo ""

# ── 判定 ──
PASS=true
if [[ "${RESTORED_TABLES:-0}" -lt 14 ]] 2>/dev/null; then PASS=false; fi
if [[ ${DATA_LATENCY_AT_RESTORE} -gt 86400 ]]; then PASS=false; fi
if [[ ${TOTAL_DURATION} -gt 14400 ]]; then PASS=false; fi
if [[ "${CHECKSUM_OK}" != "true" ]]; then PASS=false; fi
if [[ ${RESTORE_EXIT} -ne 0 ]]; then PASS=false; fi

if [[ "${PASS}" == "true" ]]; then
  echo "[drill] ✅ DRILL PASSED"
  exit 0
else
  echo "[drill] ❌ DRILL FAILED" >&2
  exit 1
fi
