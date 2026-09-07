#!/usr/bin/env bash
# infra/backup/cleanup.sh — 按 ADR-005 保留策略清理过期备份
#
# 用法：
#   cleanup.sh
#
# 环境变量：
#   BACKUP_DIR              本地备份目录（必需）
#   RETENTION_LOCAL_DAYS    本地保留天数（默认 7）
#   OFFSITE_DIR             异地副本目录（设置后同步清理）
#   RETENTION_OFFSITE_DAYS  异地保留天数（默认 90）
#   SUCCESS_MARKER          成功标记路径（默认 ${BACKUP_DIR}/.backup_succeeded）
#   FAILURE_MARKER          失败标记路径（默认 ${BACKUP_DIR}/.backup_failed）
#   PROMETHEUS_TEXTFILE_DIR 设置后向该目录写入 Prometheus textfile 指标（.prom）

# 使用 set -eu（不启用 pipefail），保持与 ash 兼容以便未来替换基础镜像。
set -eu

if [ -z "${BACKUP_DIR:-}" ]; then
  echo "ERROR: BACKUP_DIR is required" >&2
  exit 1
fi

if [ ! -d "${BACKUP_DIR}" ]; then
  echo "ERROR: BACKUP_DIR (${BACKUP_DIR}) does not exist" >&2
  exit 1
fi

RETENTION_LOCAL_DAYS="${RETENTION_LOCAL_DAYS:-7}"
RETENTION_OFFSITE_DAYS="${RETENTION_OFFSITE_DAYS:-90}"
SUCCESS_MARKER="${SUCCESS_MARKER:-${BACKUP_DIR}/.backup_succeeded}"
FAILURE_MARKER="${FAILURE_MARKER:-${BACKUP_DIR}/.backup_failed}"

echo "[cleanup] Starting cleanup at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── 清理本地备份 ──
# 注：find -mtime +N 的语义是"修改时间严格大于 N*24 小时前"
# （GNU find 忽略小数部分，因此 -mtime +6 匹配 >= 7 天前的文件）。
# 为与 ADR-005 "保留 7 天" 的语义一致，这里使用 $RETENTION_LOCAL_DAYS - 1。
LOCAL_THRESHOLD=$((RETENTION_LOCAL_DAYS - 1))
echo "[cleanup] Removing local backups older than ${RETENTION_LOCAL_DAYS} days from ${BACKUP_DIR} (mtime +${LOCAL_THRESHOLD})"

TMP_LIST=""
trap 'rm -f "${TMP_LIST:-}" 2>/dev/null' EXIT
TMP_LIST="$(mktemp)"
find "${BACKUP_DIR}" -maxdepth 1 -type f \( -name '*.dump' -o -name '*.dump.enc' -o -name '*.dump.sha256' \) -mtime +"${LOCAL_THRESHOLD}" > "${TMP_LIST}" 2>/dev/null || true

DELETED_LOCAL=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  echo "[cleanup]   Removing: $(basename "${f}")"
  rm -f "${f}"
  DELETED_LOCAL=$((DELETED_LOCAL + 1))
done < "${TMP_LIST}"
echo "[cleanup] Deleted ${DELETED_LOCAL} local file(s)"

# ── 清理异地副本 ──
if [ -n "${OFFSITE_DIR:-}" ] && [ -d "${OFFSITE_DIR}" ]; then
  OFFSITE_THRESHOLD=$((RETENTION_OFFSITE_DAYS - 1))
  echo "[cleanup] Removing offsite backups older than ${RETENTION_OFFSITE_DAYS} days from ${OFFSITE_DIR} (mtime +${OFFSITE_THRESHOLD})"
  rm -f "${TMP_LIST}"
  find "${OFFSITE_DIR}" -maxdepth 1 -type f \( -name '*.dump' -o -name '*.dump.enc' -o -name '*.dump.sha256' \) -mtime +"${OFFSITE_THRESHOLD}" > "${TMP_LIST}" 2>/dev/null || true
  DELETED_OFFSITE=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    echo "[cleanup]   Removing: $(basename "${f}")"
    rm -f "${f}"
    DELETED_OFFSITE=$((DELETED_OFFSITE + 1))
  done < "${TMP_LIST}"
  echo "[cleanup] Deleted ${DELETED_OFFSITE} offsite file(s)"
fi

# ── 清理失败标记文件（仅当本轮 backup 成功时才清除）──
# 这样可避免失败信号被静默掩盖：若 backup 失败留下 FAILURE_MARKER，
# cleanup.sh 不会提前抹掉它，直到下一次 backup 成功写入 SUCCESS_MARKER。
if [ -f "${SUCCESS_MARKER}" ]; then
  echo "[cleanup] Removing failure marker (backup succeeded)"
  rm -f "${FAILURE_MARKER}" 2>/dev/null || true
else
  if [ -f "${FAILURE_MARKER}" ]; then
    echo "[cleanup] WARNING: FAILURE_MARKER present and no SUCCESS_MARKER; keeping failure signal" >&2
  fi
fi

# ── Prometheus textfile 指标 ──
# 注：backup_files_count 由 backup.sh 写入 backup_metrics.prom，
# 此处不再重复定义，避免 Prometheus textfile collector 遇到同目录
# 多文件重复定义同一 metric 的冲突（NEW-1 修复）。
if [ -n "${PROMETHEUS_TEXTFILE_DIR:-}" ]; then
  mkdir -p "${PROMETHEUS_TEXTFILE_DIR}" 2>/dev/null || true
  echo "[cleanup] Metrics skipped (backup_files_count provided by backup.sh)"
fi

echo "[cleanup] Cleanup completed at $(date -u +%Y-%m-%dT%H:%M:%SZ)"
