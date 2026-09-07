#!/bin/sh
# infra/backup/verify-scheduler.sh — UTC 04:00 锚定的校验调度器
#
# 行为：
#   1. 计算到下一个 UTC 04:00 的秒数并 sleep
#   2. 运行 verify-backup.sh（完整恢复 + Alembic + 14 表断言）
#   3. 回到 1
#
# 环境变量透传给 verify-backup.sh：
#   BACKUP_DIR / PGHOST / PGPORT / PGUSER / PGPASSWORD /
#   PROJECT_ROOT / BACKUP_PASSPHRASE / PROMETHEUS_TEXTFILE_DIR / STRICT_ALEMBIC

set -eu

SCRIPTS_DIR="${SCRIPTS_DIR:-$(cd "$(dirname "$0")" && pwd)}"

seconds_until_utc() {
  _target_h=$1
  _target_m=${2:-0}

  _now_h=$(date -u +%H); _now_h=${_now_h#0}; : "${_now_h:=0}"
  _now_m=$(date -u +%M); _now_m=${_now_m#0}; : "${_now_m:=0}"
  _now_s=$(date -u +%S); _now_s=${_now_s#0}; : "${_now_s:=0}"

  _now_secs=$(( _now_h * 3600 + _now_m * 60 + _now_s ))
  _target_secs=$(( _target_h * 3600 + _target_m * 60 ))
  _wait=$(( _target_secs - _now_secs ))
  if [ $_wait -le 0 ]; then
    _wait=$(( _wait + 86400 ))
  fi
  echo "$_wait"
}

echo "[verify-scheduler] Starting at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

while true; do
  WAIT_SECS=$(seconds_until_utc 4 0)
  echo "[verify-scheduler] Sleeping ${WAIT_SECS}s until next 04:00 UTC (now: $(date -u +%H:%M:%S))"
  sleep "${WAIT_SECS}"

  echo "[verify-scheduler] Running scheduled verification at $(date -u +%Y-%m-%dT%H:%M:%SZ)..."
  if ! sh "${SCRIPTS_DIR}/verify-backup.sh"; then
    echo "[verify-scheduler] ERROR: verification failed (exit $?)" >&2
  fi
done
