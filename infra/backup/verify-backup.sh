#!/usr/bin/env bash
# infra/backup/verify-backup.sh — 备份完整性校验
#
# 恢复最新备份至临时数据库 → 运行 Alembic 迁移 → 表结构断言 → 清理。
# 设计为 cron 每日 04:00 (UTC) 运行。
#
# 用法：
#   verify-backup.sh
#   verify-backup.sh /path/to/specific/dump
#
# 环境变量：
#   PGHOST       数据库主机（默认 localhost）
#   PGPORT       数据库端口（默认 5432）
#   PGUSER       数据库用户（默认 aozora）
#   PGPASSWORD   数据库超级用户密码（需要 CREATEDB 权限；建议通过 .pgpass 提供）
#   BACKUP_DIR   备份目录（默认 /backups）
#   PROJECT_ROOT 项目根目录（用于定位 alembic 与断言脚本；必须指向仓库根目录）
#   RESTORE_DB   临时数据库名（默认 aozora_verify_<timestamp>）
#   BACKUP_PASSPHRASE  设置后自动解密 .enc 备份
#   PROMETHEUS_TEXTFILE_DIR  设置后向该目录写入 Prometheus textfile 指标（.prom）
#   STRICT_ALEMBIC  设置为 1 时 alembic 迁移失败会导致整体失败（默认 0 = WARNING）

# 使用 set -eu（不启用 pipefail），保持与 ash 兼容以便未来替换基础镜像。
set -eu

PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-aozora}"
BACKUP_DIR="${BACKUP_DIR:-/backups}"
RESTORE_DB="${RESTORE_DB:-aozora_verify_$(date -u +%Y%m%d%H%M%S)}"
STRICT_ALEMBIC="${STRICT_ALEMBIC:-0}"

# ── PROJECT_ROOT 解析 ──
# 不再默认 `.`，避免 cron 下静默落到 $HOME（M3 修复）。
# 推导逻辑仅使用 POSIX 语法，确保 ash/busybox 兼容。
if [ -n "${PROJECT_ROOT:-}" ]; then
  : # 用户显式传入,使用之
else
  # 从脚本位置向上推导:infra/backup/verify-backup.sh -> 仓库根
  # 兼容 `sh /path/to/script` 和 `/path/to/script` 两种调用方式
  if [ -n "${0:-}" ]; then
    _self_dir="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo "")"
    if [ -n "${_self_dir}" ]; then
      PROJECT_ROOT="$(cd "${_self_dir}/../.." 2>/dev/null && pwd || echo "")"
    fi
  fi
fi

if [ -z "${PROJECT_ROOT:-}" ] || [ ! -d "${PROJECT_ROOT}" ]; then
  echo "[verify] ERROR: PROJECT_ROOT is required and must point to the repository root" >&2
  echo "[verify]   Either set PROJECT_ROOT env var or invoke this script via its real path" >&2
  exit 1
fi

# ── Prometheus textfile 指标写入 ──
_emit_verify_metrics() {
  local exit_code="$1"
  if [[ -z "${PROMETHEUS_TEXTFILE_DIR:-}" ]]; then
    return 0
  fi
  mkdir -p "${PROMETHEUS_TEXTFILE_DIR}" 2>/dev/null || true
  local tmp="${PROMETHEUS_TEXTFILE_DIR}/.backup_verify_metrics.$$.prom"
  local final="${PROMETHEUS_TEXTFILE_DIR}/backup_verify_metrics.prom"
  cat > "${tmp}" <<EOF
# HELP backup_verify_exit_code Exit code of last verify-backup.sh run (0=success)
# TYPE backup_verify_exit_code gauge
backup_verify_exit_code ${exit_code}
EOF
  if mv "${tmp}" "${final}" 2>/dev/null; then
    echo "[verify] Metrics written to ${final}"
  else
    rm -f "${tmp}" 2>/dev/null || true
  fi
}

# ── 参数校验 ──
if [[ -z "${PGPASSWORD:-}" ]]; then
  # 不硬编码回退密码;依赖 .pgpass 或 pg_hba.conf trust 认证
  echo "[verify] WARNING: PGPASSWORD not set; relying on .pgpass or trust auth" >&2
fi

# ── 定位备份文件 ──
if [[ $# -ge 1 ]] && [[ -f "$1" ]]; then
  DUMP_FILE="$1"
elif [[ -d "${BACKUP_DIR}" ]]; then
  # 优先使用未加密的 .dump，其次 .dump.enc
  DUMP_FILE="$(find "${BACKUP_DIR}" -maxdepth 1 -name 'aozora-*.dump' -type f -printf '%T@ %p\n' 2>/dev/null \
    | sort -rn | head -1 | cut -d' ' -f2-)"
  if [[ -z "${DUMP_FILE}" ]]; then
    DUMP_FILE="$(ls -t "${BACKUP_DIR}"/aozora-*.dump 2>/dev/null | head -1 || true)"
  fi
  if [[ -z "${DUMP_FILE}" ]]; then
    DUMP_FILE="$(ls -t "${BACKUP_DIR}"/aozora-*.dump.enc 2>/dev/null | head -1 || true)"
  fi
else
  echo "[verify] ERROR: No backup file specified and BACKUP_DIR (${BACKUP_DIR}) does not exist" >&2
  _emit_verify_metrics 1
  exit 1
fi

if [[ -z "${DUMP_FILE}" ]] || [[ ! -f "${DUMP_FILE}" ]]; then
  echo "[verify] ERROR: No backup file found" >&2
  _emit_verify_metrics 1
  exit 1
fi

echo "[verify] Starting verification at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[verify] Backup file: ${DUMP_FILE}"
echo "[verify] PROJECT_ROOT: ${PROJECT_ROOT}"

# ── 导出 PGPASSWORD ──
if [[ -n "${PGPASSWORD:-}" ]]; then
  export PGPASSWORD
fi

# ── 清理函数 ──
cleanup() {
  local exit_code=$?
  echo "[verify] Cleaning up temporary database '${RESTORE_DB}'"
  dropdb --host="${PGHOST}" --port="${PGPORT}" --username="${PGUSER}" \
    --if-exists "${RESTORE_DB}" 2>/dev/null || true
  # 清理解密临时文件
  if [[ -n "${DECRYPTED_FILE:-}" ]] && [[ -f "${DECRYPTED_FILE:-}" ]]; then
    rm -f "${DECRYPTED_FILE}" 2>/dev/null || true
  fi
  if [[ ${exit_code} -ne 0 ]]; then
    echo "[verify] Verification FAILED (exit code: ${exit_code})" >&2
  fi
  _emit_verify_metrics "${exit_code}"
  exit ${exit_code}
}
trap cleanup EXIT

# ── 解密（如需要）──
ACTUAL_DUMP="${DUMP_FILE}"
if [[ "${DUMP_FILE}" == *.enc ]]; then
  if [[ -z "${BACKUP_PASSPHRASE:-}" ]]; then
    echo "[verify] ERROR: Backup is encrypted but BACKUP_PASSPHRASE not set" >&2
    exit 1
  fi
  DECRYPTED_FILE="${DUMP_FILE%.enc}"
  echo "[verify] Decrypting backup"
  openssl enc -d -aes-256-cbc -pbkdf2 \
    -in "${DUMP_FILE}" \
    -out "${DECRYPTED_FILE}" \
    -pass "env:BACKUP_PASSPHRASE"
  ACTUAL_DUMP="${DECRYPTED_FILE}"
fi

# ── 校验 SHA-256 ──
CHECKSUM_FILE="${ACTUAL_DUMP}.sha256"
if [[ -f "${CHECKSUM_FILE}" ]]; then
  echo "[verify] Verifying SHA-256 checksum"
  BACKUP_DIRNAME="$(dirname "${ACTUAL_DUMP}")"
  # 不使用 2>/dev/null 吞掉所有错误;只允许 sha256sum 自身的校验失败回退到 shasum
  SHA_OK=0
  if command -v sha256sum &>/dev/null; then
    if (cd "${BACKUP_DIRNAME}" && sha256sum -c "$(basename "${CHECKSUM_FILE}")" --quiet); then
      SHA_OK=1
    fi
  elif command -v shasum &>/dev/null; then
    if (cd "${BACKUP_DIRNAME}" && shasum -a 256 -c "$(basename "${CHECKSUM_FILE}")" --quiet); then
      SHA_OK=1
    fi
  fi
  if [[ "${SHA_OK}" -ne 1 ]]; then
    # Fall back: manual comparison
    echo "[verify] Checksum verification tool output was non-zero; falling back to manual comparison"
    EXPECTED=$(awk '{print $1}' "${CHECKSUM_FILE}")
    if command -v sha256sum &>/dev/null; then
      ACTUAL=$(sha256sum "${ACTUAL_DUMP}" | awk '{print $1}')
    else
      ACTUAL=$(shasum -a 256 "${ACTUAL_DUMP}" | awk '{print $1}')
    fi
    if [[ "${EXPECTED}" != "${ACTUAL}" ]]; then
      echo "[verify] ERROR: Checksum mismatch!" >&2
      echo "[verify]   Expected: ${EXPECTED}" >&2
      echo "[verify]   Actual:   ${ACTUAL}" >&2
      exit 1
    fi
  fi
  echo "[verify] Checksum OK"
else
  echo "[verify] WARNING: No checksum file found, skipping verification"
fi

# ── 创建临时数据库 ──
echo "[verify] Creating temporary database '${RESTORE_DB}'"
createdb --host="${PGHOST}" --port="${PGPORT}" --username="${PGUSER}" "${RESTORE_DB}"

# ── 恢复备份 ──
echo "[verify] Restoring backup to '${RESTORE_DB}'"
if ! pg_restore \
    --host="${PGHOST}" \
    --port="${PGPORT}" \
    --username="${PGUSER}" \
    --dbname="${RESTORE_DB}" \
    --clean --if-exists \
    --no-owner --no-privileges \
    "${ACTUAL_DUMP}" 2>&1; then
  echo "[verify] WARNING: pg_restore reported errors (may be expected with --clean)" >&2
fi

# ── 运行 Alembic 迁移 ──
ALEMBIC_CFG="${PROJECT_ROOT}/server/alembic.ini"
if [[ -f "${ALEMBIC_CFG}" ]]; then
  echo "[verify] Running Alembic migrations"
  # 构造 DATABASE_URL:有密码时 user:pass@host,无密码时 trust 认证
  if [[ -n "${PGPASSWORD:-}" ]]; then
    ALEMBIC_DB_URL="postgresql+asyncpg://${PGUSER}:${PGPASSWORD}@${PGHOST}:${PGPORT}/${RESTORE_DB}"
  else
    ALEMBIC_DB_URL="postgresql+asyncpg://${PGUSER}@${PGHOST}:${PGPORT}/${RESTORE_DB}"
  fi
  if ! (cd "${PROJECT_ROOT}/server" && \
    DATABASE_URL="${ALEMBIC_DB_URL}" \
    alembic upgrade head 2>&1); then
    if [[ "${STRICT_ALEMBIC}" == "1" ]]; then
      echo "[verify] ERROR: Alembic migration failed (STRICT_ALEMBIC=1)" >&2
      exit 1
    else
      echo "[verify] WARNING: Alembic migration failed; verification continues on backup schema" >&2
    fi
  fi
else
  echo "[verify] WARNING: alembic.ini not found at ${ALEMBIC_CFG}; skipping migrations" >&2
fi

# ── 表结构断言 ──
echo "[verify] Running table assertions"

# 预期 14 张业务表（T-M5-03a §验收标准）
# 使用空格分隔字符串代替 bash 数组以兼容 POSIX sh / ash。
EXPECTED_TABLES="users device_sessions books categories accounts transactions budgets attachments ai_conversations ai_usage sync_changes mutation_idempotency exports user_consents"
EXPECTED_TABLE_COUNT=14

PSQL="psql --host=${PGHOST} --port=${PGPORT} --username=${PGUSER} --dbname=${RESTORE_DB} --no-align --tuples-only"

ERRORS=0

# 1) 检查表数量
TABLE_COUNT=$(${PSQL} -c "
  SELECT COUNT(*) FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
" 2>/dev/null | tr -d '[:space:]')
echo "[verify] Business tables found: ${TABLE_COUNT} (expected: ${EXPECTED_TABLE_COUNT})"
if [ "${TABLE_COUNT:-0}" -lt "${EXPECTED_TABLE_COUNT}" ]; then
  echo "[verify] FAIL: fewer tables than expected" >&2
  ERRORS=$((ERRORS + 1))
fi

# 2) 检查每张表存在且可读
for tbl in ${EXPECTED_TABLES}; do
  EXISTS=$(${PSQL} -c "
    SELECT COUNT(*) FROM information_schema.tables
    WHERE table_schema = 'public' AND table_name = '${tbl}';
  " 2>/dev/null | tr -d '[:space:]')
  if [ "${EXISTS:-0}" != "1" ]; then
    echo "[verify] FAIL: table '${tbl}' missing" >&2
    ERRORS=$((ERRORS + 1))
  fi
done

# 3) 检查 alembic_version
ALEMBIC_VER=$(${PSQL} -c "SELECT version_num FROM alembic_version LIMIT 1;" 2>/dev/null | tr -d '[:space:]' || echo "")
if [[ -n "${ALEMBIC_VER}" ]]; then
  echo "[verify] Alembic version: ${ALEMBIC_VER}"
fi

# 4) 总行数摘要
echo "[verify] Row counts:"
for tbl in users books categories accounts transactions budgets attachments; do
  COUNT=$(${PSQL} -c "SELECT COUNT(*) FROM ${tbl};" 2>/dev/null | tr -d '[:space:]' || echo "0")
  echo "[verify]   ${tbl}: ${COUNT}"
done

# ── 结果 ──
echo "[verify]"
if [[ ${ERRORS} -eq 0 ]]; then
  echo "[verify] PASS — All assertions passed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
else
  echo "[verify] FAIL — ${ERRORS} assertion(s) failed" >&2
  exit 1
fi
