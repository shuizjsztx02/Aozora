#!/usr/bin/env bash
# infra/backup/backup-scheduler.sh — UTC 03:00 锚定的备份调度器
#
# 用法：
#   backup-scheduler.sh
#
# 行为：
#   1. 立即运行一次备份（首次启动立即可用）
#   2. 计算到下一个 UTC 03:00 的秒数并 sleep
#   3. 运行 backup.sh + cleanup.sh
#   4. 回到 2
#
# 环境变量透传给 backup.sh / cleanup.sh：
#   BACKUP_DIR / PGHOST / PGPORT / PGUSER / PGDATABASE / PGPASSWORD /
#   OFFSITE_DIR / BACKUP_PASSPHRASE / RETENTION_LOCAL_DAYS / RETENTION_OFFSITE_DAYS /
#   FAILURE_MARKER / SUCCESS_MARKER / PROMETHEUS_TEXTFILE_DIR

set -eu

SCRIPTS_DIR="${SCRIPTS_DIR:-$(cd "$(dirname "$0")" && pwd)}"

# ── 计算到下一个 HH:MM UTC 的秒数 ──
# 使用 POSIX 兼容语法；通过 ${var#0} 去除单前导零以避免 shell 算术将 "08"/"09" 误判为八进制
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

echo "[backup-scheduler] Starting at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# 首次启动立即运行一次备份（首次安装后尽快产生可验证备份）
# NEW-2 修复：backup.sh / cleanup.sh 使用 bash 特性，改用 bash 调用
echo "[backup-scheduler] Running initial backup..."
if bash "${SCRIPTS_DIR}/backup.sh"; then
  bash "${SCRIPTS_DIR}/cleanup.sh" || echo "[backup-scheduler] WARNING: cleanup.sh failed (non-fatal)" >&2
else
  echo "[backup-scheduler] WARNING: initial backup failed; will retry at 03:00 UTC" >&2
fi

# 主调度循环：每次 sleep 到下一个 UTC 03:00
while true; do
  WAIT_SECS=$(seconds_until_utc 3 0)
  echo "[backup-scheduler] Sleeping ${WAIT_SECS}s until next 03:00 UTC (now: $(date -u +%H:%M:%S))"
  sleep "${WAIT_SECS}"

  echo "[backup-scheduler] Running scheduled backup at $(date -u +%Y-%m-%dT%H:%M:%SZ)..."
  # NEW-2 修复：backup.sh / cleanup.sh 使用 bash 特性，改用 bash 调用
  if bash "${SCRIPTS_DIR}/backup.sh"; then
    bash "${SCRIPTS_DIR}/cleanup.sh" || echo "[backup-scheduler] WARNING: cleanup.sh failed (non-fatal)" >&2
  else
    echo "[backup-scheduler] ERROR: scheduled backup failed (exit $?)" >&2
  fi
done
