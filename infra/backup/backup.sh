#!/usr/bin/env bash
# infra/backup/backup.sh — ADR-005 定时备份脚本
#
# 用法：
#   backup.sh                        # 默认连接本地 PostgreSQL
#   PGHOST=db PGUSER=aozora ... backup.sh
#
# 必需环境变量：
#   BACKUP_DIR    备份输出目录
#
# 可选环境变量：
#   PGHOST        数据库主机（默认 localhost）
#   PGPORT        数据库端口（默认 5432）
#   PGUSER        数据库用户（默认 aozora）
#   PGDATABASE    数据库名（默认 aozora）
#   PGPASSWORD    数据库密码（建议用 .pgpass 或 pg_hba.conf trust 认证）
#   OFFSITE_DIR   异地副本目录（设置后自动复制）
#   BACKUP_PASSPHRASE  设置后使用 openssl aes-256-cbc 加密备份
#   RETENTION_LOCAL_DAYS  本地保留天数（默认 7）
#   RETENTION_OFFSITE_DAYS  异地保留天数（默认 90）
#   FAILURE_MARKER   失败标记文件路径（默认 ${BACKUP_DIR}/.backup_failed）
#   SUCCESS_MARKER   成功标记文件路径（默认 ${BACKUP_DIR}/.backup_succeeded）
#   PROMETHEUS_TEXTFILE_DIR  设置后向该目录写入 Prometheus textfile 指标（.prom）
#
# 退出码：
#   0  成功
#   1  通用错误（参数缺失等）
#   2  pg_dump 或加密失败
#   3  校验和计算失败

# Alpine (postgres:18-alpine) uses busybox ash which does NOT support
# `set -o pipefail`. Use `set -eu` to remain sh-compatible.
set -eu

# ── 参数校验（先于所有默认值展开，避免 BACKUP_DIR 未设置时落到 /tmp）──
if [[ -z "${BACKUP_DIR:-}" ]]; then
  echo "ERROR: BACKUP_DIR is required" >&2
  exit 1
fi

mkdir -p "${BACKUP_DIR}"

# ── 默认值（依赖 BACKUP_DIR，已校验）──
PGHOST="${PGHOST:-localhost}"
PGPORT="${PGPORT:-5432}"
PGUSER="${PGUSER:-aozora}"
PGDATABASE="${PGDATABASE:-aozora}"
RETENTION_LOCAL_DAYS="${RETENTION_LOCAL_DAYS:-7}"
RETENTION_OFFSITE_DAYS="${RETENTION_OFFSITE_DAYS:-90}"
FAILURE_MARKER="${FAILURE_MARKER:-${BACKUP_DIR}/.backup_failed}"
SUCCESS_MARKER="${SUCCESS_MARKER:-${BACKUP_DIR}/.backup_succeeded}"

TIMESTAMP="$(date -u +%Y%m%d-%H%M%S)"

# ── Prometheus textfile 指标写入辅助函数 ──
# 当 PROMETHEUS_TEXTFILE_DIR 设置时，向该目录写入原子 .prom 文件
# 供 node_exporter textfile-collector 或 Prometheus agent 消费。
_emit_metrics() {
  local exit_code="$1"
  local success_ts="$2"
  local files_count="$3"
  if [[ -z "${PROMETHEUS_TEXTFILE_DIR:-}" ]]; then
    return 0
  fi
  mkdir -p "${PROMETHEUS_TEXTFILE_DIR}" 2>/dev/null || true
  local tmp="${PROMETHEUS_TEXTFILE_DIR}/.backup_metrics.$$.prom"
  local final="${PROMETHEUS_TEXTFILE_DIR}/backup_metrics.prom"
  cat > "${tmp}" <<EOF
# HELP backup_last_success_timestamp_seconds Unix timestamp of last successful backup
# TYPE backup_last_success_timestamp_seconds gauge
backup_last_success_timestamp_seconds ${success_ts}
# HELP backup_last_exit_code Exit code of last backup.sh run (0=success)
# TYPE backup_last_exit_code gauge
backup_last_exit_code ${exit_code}
# HELP backup_files_count Number of backup files in BACKUP_DIR
# TYPE backup_files_count gauge
backup_files_count ${files_count}
EOF
  if mv "${tmp}" "${final}" 2>/dev/null; then
    echo "[backup] Metrics written to ${final}"
  else
    rm -f "${tmp}" 2>/dev/null || true
    echo "[backup] WARNING: failed to write metrics file" >&2
  fi
}

_count_backup_files() {
  # 统计 BACKUP_DIR 中 aozora-*.dump / .dump.enc 文件数量
  local n=0
  for f in "${BACKUP_DIR}"/aozora-*.dump "${BACKUP_DIR}"/aozora-*.dump.enc; do
    [[ -f "$f" ]] && n=$((n + 1))
  done
  echo "${n}"
}

DUMP_FILE="${BACKUP_DIR}/aozora-${TIMESTAMP}.dump"
CHECKSUM_FILE="${DUMP_FILE}.sha256"

# ── 清除旧标记（仅清除与本次运行关联的标记）──
rm -f "${FAILURE_MARKER}" "${SUCCESS_MARKER}"

# ── 执行 pg_dump ──
echo "[backup] Starting pg_dump at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "[backup] Target: ${PGHOST}:${PGPORT}/${PGDATABASE}"

if ! pg_dump \
    --host="${PGHOST}" \
    --port="${PGPORT}" \
    --username="${PGUSER}" \
    --dbname="${PGDATABASE}" \
    --format=custom \
    --compress=6 \
    --no-owner \
    --no-privileges \
    --exclude-table-data='ai_usage' \
    --file="${DUMP_FILE}" 2>&1; then
  echo "[backup] ERROR: pg_dump failed" >&2
  touch "${FAILURE_MARKER}"
  _emit_metrics 2 0 "$(_count_backup_files)"
  exit 2
fi

DUMP_SIZE=$(stat -c%s "${DUMP_FILE}" 2>/dev/null \
  || stat -f%z "${DUMP_FILE}" 2>/dev/null \
  || echo "unknown")
echo "[backup] pg_dump completed: ${DUMP_FILE} (${DUMP_SIZE} bytes)"

# ── 可选加密 ──
if [[ -n "${BACKUP_PASSPHRASE:-}" ]]; then
  echo "[backup] Encrypting backup with aes-256-cbc"
  if ! openssl enc -aes-256-cbc -salt -pbkdf2 \
      -in "${DUMP_FILE}" \
      -out "${DUMP_FILE}.enc" \
      -pass "env:BACKUP_PASSPHRASE" 2>&1; then
    echo "[backup] ERROR: encryption failed" >&2
    touch "${FAILURE_MARKER}"
    _emit_metrics 2 0 "$(_count_backup_files)"
    exit 2
  fi
  rm -f "${DUMP_FILE}"
  DUMP_FILE="${DUMP_FILE}.enc"
  echo "[backup] Encrypted: ${DUMP_FILE}"
fi

# ── 计算 SHA-256 校验和 ──
echo "[backup] Computing SHA-256 checksum"
if command -v sha256sum &>/dev/null; then
  (cd "${BACKUP_DIR}" && sha256sum "$(basename "${DUMP_FILE}")" > "$(basename "${CHECKSUM_FILE}")")
elif command -v shasum &>/dev/null; then
  (cd "${BACKUP_DIR}" && shasum -a 256 "$(basename "${DUMP_FILE}")" > "$(basename "${CHECKSUM_FILE}")")
else
  echo "[backup] WARNING: no sha256 tool found, skipping checksum" >&2
  touch "${FAILURE_MARKER}"
  _emit_metrics 3 0 "$(_count_backup_files)"
  exit 3
fi
echo "[backup] Checksum: $(cat "${CHECKSUM_FILE}" 2>/dev/null || echo '(unreadable)')"

# ── 异地副本 ──
if [[ -n "${OFFSITE_DIR:-}" ]]; then
  echo "[backup] Replicating to offsite: ${OFFSITE_DIR}"
  mkdir -p "${OFFSITE_DIR}"
  cp -v "${DUMP_FILE}" "${OFFSITE_DIR}/"
  cp -v "${CHECKSUM_FILE}" "${OFFSITE_DIR}/" 2>/dev/null || true
  echo "[backup] Offsite replication complete"
fi

# ── 成功 ──
touch "${SUCCESS_MARKER}"
SUCCESS_TS="$(date +%s)"
_emit_metrics 0 "${SUCCESS_TS}" "$(_count_backup_files)"
echo "[backup] Backup completed successfully at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
