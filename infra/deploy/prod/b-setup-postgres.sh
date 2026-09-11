#!/usr/bin/env bash
# b-setup-postgres.sh — 服务器 B（Docker PostgreSQL 双实例）上执行。
#
# 前提：B 机已有 pg-prod（5432）/ pg-test（15432）容器，且其初始化管理员
# 由容器环境变量 POSTGRES_USER 指定。脚本不读取管理员密码文件：容器内本地
# socket 使用 trust，管理员仅用于创建低权限 API 账号和重载配置。
#
# 作用（幂等，可重复执行）：
#   1. 保留现有 prod_db/test_db，不删除、不重建数据卷或初始化管理员；
#   2. 创建 aozora_api_prod/aozora_api_test 两个低权限 API 账号；
#   3. 将 initdb 默认的远程宽规则收紧为仅 A 机访问对应库/账号；
#   4. 输出可直接粘贴到服务器 A .env 的 DATABASE_URL。
#
# 以 root 执行：bash infra/deploy/prod/b-setup-postgres.sh
set -euo pipefail

SERVER_A_IP="${SERVER_A_IP:-192.168.44.13}"
PW_FILE="/root/aozora-app-db-passwords.txt"
PROD_CONTAINER="${PROD_CONTAINER:-pg-prod}"
TEST_CONTAINER="${TEST_CONTAINER:-pg-test}"
PROD_ROLE="aozora_api_prod"
TEST_ROLE="aozora_api_test"

log()  { echo "[INFO]  $*"; }
fail() { echo "[FAIL]  $*" >&2; exit 1; }

command -v docker >/dev/null || fail "本机没有 Docker"
command -v openssl >/dev/null || fail "本机没有 openssl，无法生成 API 账号密码"
[ "$(id -u)" -eq 0 ] || fail "请以 root 执行本脚本"

validate_identifier() {
  [[ "$1" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail "数据库或角色名不安全：$1"
}

container_running() {
  [ "$(docker inspect -f '{{.State.Running}}' "$1" 2>/dev/null || true)" = "true" ]
}

container_env() { # container_env <container> <POSTGRES_USER|POSTGRES_DB>
  docker exec "$1" sh -c 'printenv "$1"' sh "$2"
}

psql_admin() { # psql_admin <container> <database> <sql>
  docker exec "$1" sh -c \
    'psql -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1 -Atqc "$2"' \
    sh "$2" "$3"
}

password_for_existing_role() {
  local role="$1"
  { grep -m 1 -E "^${role}=" "$PW_FILE" 2>/dev/null || true; } | cut -d= -f2-
}

ensure_api_role() { # ensure_api_role <container> <database> <role>
  local container="$1" db="$2" role="$3" pw existing_pw

  if [ "$(psql_admin "$container" postgres "SELECT 1 FROM pg_database WHERE datname='${db}'")" != "1" ]; then
    fail "${container} 中没有初始化数据库 ${db}；为保护现有实例，脚本不会自行创建或替换数据库"
  fi

  if [ "$(psql_admin "$container" postgres "SELECT 1 FROM pg_roles WHERE rolname='${role}'")" = "1" ]; then
    existing_pw="$(password_for_existing_role "$role")"
    [ -n "$existing_pw" ] || fail "${role} 已存在但 ${PW_FILE} 未存密码；为避免覆盖未知密码，停止执行"
    pw="$existing_pw"
    log "${container} 的 API 账号 ${role} 已存在，沿用 ${PW_FILE} 中密码"
  else
    pw="$(openssl rand -hex 24)"
    psql_admin "$container" postgres \
      "CREATE ROLE ${role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '${pw}'"
    printf '%s=%s\n' "$role" "$pw" >> "$PW_FILE"
    log "${container} 的低权限 API 账号 ${role} 已创建"
  fi

  # Alembic 由 API 账号执行，需连接数据库及在 public schema 中建迁移表/业务表。
  psql_admin "$container" postgres "GRANT CONNECT, TEMPORARY ON DATABASE ${db} TO ${role}"
  psql_admin "$container" "$db" "GRANT USAGE, CREATE ON SCHEMA public TO ${role}"
  INSTANCE_PW="$pw"
}

restrict_hba() { # restrict_hba <container> <database> <role>
  local container="$1" db="$2" role="$3" hba backup_suffix wide_count
  hba="$(psql_admin "$container" postgres 'SHOW hba_file')"
  backup_suffix="$(date +%Y%m%d%H%M%S)"
  wide_count="$(psql_admin "$container" postgres "SELECT count(*) FROM pg_hba_file_rules WHERE type = 'host' AND address::text = 'all'")"

  if [ "$wide_count" = "0" ]; then
    log "${container} 未发现 initdb 默认的远程宽规则，保留现有 HBA 并仅校验目标规则"
  elif [ "$wide_count" = "1" ]; then
    docker exec "$container" sh -c 'cp "$1" "$1.bak.$2"' sh "$hba" "$backup_suffix"
    docker exec "$container" sed -i -E \
      "s|^[[:space:]]*host[[:space:]]+all[[:space:]]+all[[:space:]]+all[[:space:]]+scram-sha-256[[:space:]]*$|host\\t${db}\\t${role}\\t${SERVER_A_IP}/32\\tscram-sha-256|" \
      "$hba"
    log "${container} 已备份 ${hba}.bak.${backup_suffix}，并收紧唯一远程宽规则"
  else
    fail "${container} 有 ${wide_count} 条远程宽规则；为避免误删其他用途规则，停止执行"
  fi

  psql_admin "$container" postgres 'SELECT pg_reload_conf()' >/dev/null

  if [ "$(psql_admin "$container" postgres "SELECT count(*) FROM pg_hba_file_rules WHERE type = 'host' AND database::text = '{${db}}' AND user_name::text = '{${role}}' AND address::text = '${SERVER_A_IP}' AND auth_method = 'scram-sha-256'")" != "1" ]; then
    fail "${container} 未找到预期 HBA 规则：${db}/${role}/${SERVER_A_IP}/32"
  fi
  if [ "$(psql_admin "$container" postgres "SELECT count(*) FROM pg_hba_file_rules WHERE type = 'host' AND address::text = 'all'")" != "0" ]; then
    fail "${container} 仍存在远程宽规则，请检查 ${hba}"
  fi
  log "${container} HBA 已验证：仅 ${SERVER_A_IP}/32 可访问 ${db}/${role}"
}

setup_instance() { # setup_instance <label> <container> <role>
  local label="$1" container="$2" role="$3" db admin
  container_running "$container" || fail "容器 ${container} 未运行"
  db="$(container_env "$container" POSTGRES_DB)"
  admin="$(container_env "$container" POSTGRES_USER)"
  validate_identifier "$db"
  validate_identifier "$admin"
  validate_identifier "$role"
  log "── ${label}：${container}（初始化库=${db}，管理员=${admin}）──"
  psql_admin "$container" postgres 'SELECT 1' >/dev/null \
    || fail "无法通过 ${container} 的本地 socket 连接初始化管理员"
  ensure_api_role "$container" "$db" "$role"
  restrict_hba "$container" "$db" "$role"
  INSTANCE_DB="$db"
}

touch "$PW_FILE"
chmod 600 "$PW_FILE"

setup_instance "生产实例" "$PROD_CONTAINER" "$PROD_ROLE"
PROD_DB="$INSTANCE_DB"
PROD_PW="$INSTANCE_PW"
setup_instance "测试实例" "$TEST_CONTAINER" "$TEST_ROLE"
TEST_DB="$INSTANCE_DB"
TEST_PW="$INSTANCE_PW"

cat <<EOF

════════════════════ 下一步：粘贴到服务器 A 的 .env ════════════════════════
TEST_DATABASE_URL=postgresql+asyncpg://${TEST_ROLE}:${TEST_PW}@192.168.44.12:15432/${TEST_DB}
PROD_DATABASE_URL=postgresql+asyncpg://${PROD_ROLE}:${PROD_PW}@192.168.44.12:5432/${PROD_DB}
════════════════════════════════════════════════════════════════════════════
（API 账号密码同步保存在 B 机 ${PW_FILE}，权限为 600；请单独备份，勿提交到 Git。）

B 机验收（从 A 机执行，通了才继续 A 机部署）：
  nc -zvw3 192.168.44.12 15432   # 测试实例内网可达
  nc -zvw3 192.168.44.12 5432    # 生产实例内网可达
EOF
