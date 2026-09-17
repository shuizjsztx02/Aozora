#!/usr/bin/env bash
# b-setup-postgres.sh — 服务器 B（Docker PostgreSQL 双实例）上执行。
#
# 前提：B 机已有 pg-prod（5432）/ pg-test（15432）容器。脚本不读取管理员
# 密码文件：容器内本地 socket 使用 trust，管理员仅用于建库、建角色与重载配置。
#
# 作用（幂等，可重复执行）：
#   1. 创建业务归属角色 aozora（NOLOGIN，仅作库 owner 标记，不可登录）；
#   2. 创建 aozora_prod / aozora_test 两个业务库（owner=aozora），已存在则保留数据；
#   3. 创建低权限 API 账号 aozora_api_prod / aozora_api_test（已存在则沿用密码）；
#   4. 授予 Alembic 所需的库/schema 权限；
#   5. 在 pg_hba 中确保"仅 A 机内网可访问对应库/账号"的规则存在（替换宽规则或追加）；
#   6. 输出可直接粘贴到服务器 A .env 的 DATABASE_URL。
#
# 不创建、不删除、不改名同实例上的其他库（如运维的 test_db / prod_db /
# fame_travel_diary）；pg_hba 规则按 (库,账号,来源IP) 三元组追加，不影响既有规则。
#
# 以 root 执行：bash infra/deploy/prod/b-setup-postgres.sh
set -euo pipefail

SERVER_A_IP="${SERVER_A_IP:-192.168.44.13}"
PW_FILE="/root/aozora-app-db-passwords.txt"
PROD_CONTAINER="${PROD_CONTAINER:-pg-prod}"
TEST_CONTAINER="${TEST_CONTAINER:-pg-test}"
PROD_DB="${PROD_DB:-aozora_prod}"
TEST_DB="${TEST_DB:-aozora_test}"
OWNER_ROLE="aozora"
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

psql_admin() { # psql_admin <container> <database> <sql>
  docker exec "$1" sh -c \
    'psql -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1 -Atqc "$2"' \
    sh "$2" "$3"
}

password_for_existing_role() {
  local role="$1"
  { grep -m 1 -E "^${role}=" "$PW_FILE" 2>/dev/null || true; } | cut -d= -f2-
}

ensure_owner_role() { # ensure_owner_role <container>
  local container="$1"
  if [ "$(psql_admin "$container" postgres "SELECT 1 FROM pg_roles WHERE rolname='${OWNER_ROLE}'")" = "1" ]; then
    log "${container} 的归属角色 ${OWNER_ROLE} 已存在"
  else
    psql_admin "$container" postgres "CREATE ROLE ${OWNER_ROLE} NOLOGIN" >/dev/null
    log "${container} 已创建归属角色 ${OWNER_ROLE}（NOLOGIN，不可登录）"
  fi
}

ensure_database() { # ensure_database <container> <db>
  local container="$1" db="$2" owner_now
  if [ "$(psql_admin "$container" postgres "SELECT 1 FROM pg_database WHERE datname='${db}'")" = "1" ]; then
    owner_now="$(psql_admin "$container" postgres "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='${db}'")"
    if [ "$owner_now" = "$OWNER_ROLE" ]; then
      log "${container} 的业务库 ${db} 已存在（owner=${owner_now}），保留现有数据"
    else
      psql_admin "$container" postgres "ALTER DATABASE ${db} OWNER TO ${OWNER_ROLE}" >/dev/null
      log "${container} 的业务库 ${db} 已存在，owner 由 ${owner_now} 改为 ${OWNER_ROLE}"
    fi
    return
  fi
  psql_admin "$container" postgres \
    "CREATE DATABASE ${db} OWNER ${OWNER_ROLE} TEMPLATE template0 ENCODING 'UTF8' LC_COLLATE 'en_US.utf8' LC_CTYPE 'en_US.utf8'" >/dev/null
  log "${container} 已创建业务库 ${db}（owner=${OWNER_ROLE}）"
}

ensure_api_role() { # ensure_api_role <container> <db> <role>
  local container="$1" db="$2" role="$3" pw existing_pw

  if [ "$(psql_admin "$container" postgres "SELECT 1 FROM pg_roles WHERE rolname='${role}'")" = "1" ]; then
    existing_pw="$(password_for_existing_role "$role")"
    [ -n "$existing_pw" ] || fail "${role} 已存在但 ${PW_FILE} 未存密码；为避免覆盖未知密码，停止执行"
    pw="$existing_pw"
    log "${container} 的 API 账号 ${role} 已存在，沿用 ${PW_FILE} 中密码"
  else
    pw="$(openssl rand -hex 24)"
    psql_admin "$container" postgres \
      "CREATE ROLE ${role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD '${pw}'" >/dev/null
    printf '%s=%s\n' "$role" "$pw" >> "$PW_FILE"
    log "${container} 的低权限 API 账号 ${role} 已创建"
  fi

  # Alembic 由 API 账号执行，需连接数据库及在 public schema 中建迁移表/业务表。
  # 注意：表 owner 因此是 API 账号本身（建表者即 owner），与库 owner aozora 无关。
  psql_admin "$container" postgres "GRANT CONNECT, TEMPORARY ON DATABASE ${db} TO ${role}" >/dev/null
  psql_admin "$container" "$db" "GRANT USAGE, CREATE ON SCHEMA public TO ${role}" >/dev/null
  INSTANCE_PW="$pw"
}

hba_rule_count() { # hba_rule_count <container> <db> <role>
  psql_admin "$1" postgres "SELECT count(*) FROM pg_hba_file_rules WHERE type = 'host' AND database::text = '{${2}}' AND user_name::text = '{${3}}' AND address::text = '${SERVER_A_IP}' AND auth_method = 'scram-sha-256'"
}

ensure_hba_rule() { # ensure_hba_rule <container> <db> <role>
  local container="$1" db="$2" role="$3" hba backup_suffix wide_count
  hba="$(psql_admin "$container" postgres 'SHOW hba_file')"

  if [ "$(hba_rule_count "$container" "$db" "$role")" = "1" ]; then
    log "${container} HBA 已有规则：${db}/${role}/${SERVER_A_IP}/32（未改动）"
    return
  fi

  backup_suffix="$(date +%Y%m%d%H%M%S)"
  docker exec "$container" sh -c 'cp "$1" "$1.bak.$2"' sh "$hba" "$backup_suffix"

  wide_count="$(psql_admin "$container" postgres "SELECT count(*) FROM pg_hba_file_rules WHERE type = 'host' AND address::text = 'all'")"
  if [ "$wide_count" = "1" ]; then
    docker exec "$container" sed -i -E \
      "s|^[[:space:]]*host[[:space:]]+all[[:space:]]+all[[:space:]]+all[[:space:]]+scram-sha-256[[:space:]]*$|host\\t${db}\\t${role}\\t${SERVER_A_IP}/32\\tscram-sha-256|" \
      "$hba"
    log "${container} 已备份 ${hba}.bak.${backup_suffix}，并将 initdb 宽规则替换为 ${db}/${role} 规则"
  elif [ "$wide_count" = "0" ]; then
    docker exec "$container" sh -c '
      hba="$1"; db="$2"; role="$3"; ip="$4"
      [ -n "$(tail -c1 "$hba")" ] && printf "\n" >> "$hba"
      printf "host\t%s\t%s\t%s/32\tscram-sha-256\n" "$db" "$role" "$ip" >> "$hba"
    ' sh "$hba" "$db" "$role" "$SERVER_A_IP"
    log "${container} 已备份 ${hba}.bak.${backup_suffix}，并追加 ${db}/${role} 规则"
  else
    fail "${container} 有 ${wide_count} 条远程宽规则；为避免误删其他用途规则，停止执行"
  fi

  psql_admin "$container" postgres 'SELECT pg_reload_conf()' >/dev/null

  if [ "$(hba_rule_count "$container" "$db" "$role")" != "1" ]; then
    fail "${container} 未找到预期 HBA 规则：${db}/${role}/${SERVER_A_IP}/32"
  fi
  if [ "$(psql_admin "$container" postgres "SELECT count(*) FROM pg_hba_file_rules WHERE type = 'host' AND address::text = 'all'")" != "0" ]; then
    fail "${container} 仍存在远程宽规则，请检查 ${hba}"
  fi
  log "${container} HBA 已验证：${SERVER_A_IP}/32 可访问 ${db}/${role}"
}

setup_instance() { # setup_instance <label> <container> <db> <role>
  local label="$1" container="$2" db="$3" role="$4" admin
  container_running "$container" || fail "容器 ${container} 未运行"
  admin="$(docker exec "$container" sh -c 'printenv POSTGRES_USER')"
  validate_identifier "$db"
  validate_identifier "$admin"
  validate_identifier "$role"
  log "── ${label}：${container}（业务库=${db}，owner=${OWNER_ROLE}，API 账号=${role}，管理员=${admin}）──"
  psql_admin "$container" postgres 'SELECT 1' >/dev/null \
    || fail "无法通过 ${container} 的本地 socket 连接管理员"
  ensure_owner_role "$container"
  ensure_database "$container" "$db"
  ensure_api_role "$container" "$db" "$role"
  ensure_hba_rule "$container" "$db" "$role"
  INSTANCE_DB="$db"
}

touch "$PW_FILE"
chmod 600 "$PW_FILE"

setup_instance "生产实例" "$PROD_CONTAINER" "$PROD_DB" "$PROD_ROLE"
PROD_DB_FINAL="$INSTANCE_DB"
PROD_PW="$INSTANCE_PW"
setup_instance "测试实例" "$TEST_CONTAINER" "$TEST_DB" "$TEST_ROLE"
TEST_DB_FINAL="$INSTANCE_DB"
TEST_PW="$INSTANCE_PW"

cat <<EOF

════════════════════ 下一步：粘贴到服务器 A 的 .env ════════════════════════
TEST_DATABASE_URL=postgresql+asyncpg://${TEST_ROLE}:${TEST_PW}@192.168.44.12:15432/${TEST_DB_FINAL}
PROD_DATABASE_URL=postgresql+asyncpg://${PROD_ROLE}:${PROD_PW}@192.168.44.12:5432/${PROD_DB_FINAL}
════════════════════════════════════════════════════════════════════════════
（API 账号密码同步保存在 B 机 ${PW_FILE}，权限为 600；请单独备份，勿提交到 Git。）

注意：本脚本只建库建账号，不搬数据。若要把旧库数据迁入新库，
见 infra/deploy/prod/RUNBOOK.md 第 4.1 节（含备份与回滚）。

B 机验收（从 A 机执行，通了才继续 A 机部署）：
  nc -zvw3 192.168.44.12 15432   # 测试实例内网可达
  nc -zvw3 192.168.44.12 5432    # 生产实例内网可达
EOF