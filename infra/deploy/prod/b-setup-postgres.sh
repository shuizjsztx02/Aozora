#!/usr/bin/env bash
# b-setup-postgres.sh — 服务器 B（数据库服务器，内网 192.168.44.12）上执行。
#
# 作用（幂等，可重复执行）：
#   1. 在生产实例(5432)与测试实例(15432)中分别创建业务角色与数据库
#      （aozora_prod/aozora_prod@aozora_prod；aozora_test/aozora_test@aozora_test）
#   2. 密码随机生成一次，存 /root/aozora-db-passwords.txt（chmod 600），重跑时复用
#   3. pg_hba.conf：备份后追加"仅允许服务器 A 内网 IP 192.168.44.13 访问本库本角色"
#   4. 检查 listen_addresses / 宽放规则 / 本机防火墙，给出处置建议（不自动重启 PG）
#   5. 末尾打印两条可直接粘贴进服务器 A `.env` 的 DATABASE_URL
#
# 前置：在 B 机以 root（或有 sudo）执行；本机有 psql；知道 postgres 超级密码
# （脚本会提示输入，或预先 export PGPASSWORD_SUPER）。
#
#   bash infra/deploy/prod/b-setup-postgres.sh
set -euo pipefail

SERVER_A_IP="${SERVER_A_IP:-192.168.44.13}"   # 允许访问 DB 的唯一来源（A 机内网 IP）
PW_FILE="/root/aozora-db-passwords.txt"

PROD_PORT=5432;  PROD_ROLE=aozora_prod;  PROD_DB=aozora_prod
TEST_PORT=15432; TEST_ROLE=aozora_test;  TEST_DB=aozora_test

log()  { echo "[INFO]  $*"; }
fail() { echo "[FAIL]  $*" >&2; exit 1; }

command -v psql >/dev/null || fail "本机没有 psql，请先安装 PostgreSQL 客户端"
[ "$(id -u)" -eq 0 ] || fail "请用 root（或 sudo）执行本脚本"

# 超级用户密码：优先环境变量，否则提示输入一次
if [ -z "${PGPASSWORD_SUPER:-}" ]; then
  read -rsp "输入 postgres 超级用户密码: " PGPASSWORD_SUPER; echo
  export PGPASSWORD_SUPER
fi

psql_admin() {  # psql_admin <port> <db> <sql>
  PGPASSWORD="$PGPASSWORD_SUPER" psql -h 127.0.0.1 -p "$1" -U postgres -d "$2" \
    -v ON_ERROR_STOP=1 -Atqc "$3"
}

touch "$PW_FILE"; chmod 600 "$PW_FILE"
get_or_create_pw() {  # get_or_create_pw <name> —— 生成一次，重跑复用
  local name="$1"
  if grep -q "^${name}=" "$PW_FILE"; then
    grep "^${name}=" "$PW_FILE" | cut -d= -f2
  else
    local pw
    pw="$(openssl rand -hex 16)"
    echo "${name}=${pw}" >> "$PW_FILE"
    echo "$pw"
  fi
}

setup_instance() {  # setup_instance <label> <port> <role> <db> <pw>
  local label="$1" port="$2" role="$3" db="$4" pw="$5"

  log "── ${label} 实例 (127.0.0.1:${port}) ──"
  psql_admin "$port" postgres "SELECT 1" >/dev/null \
    || fail "无法以 postgres 超级用户连接 ${label} 实例端口 ${port}"

  # 1) 角色与库（幂等；已存在则不重置密码——密码以 PW_FILE 为准）
  if [ "$(psql_admin "$port" postgres "SELECT 1 FROM pg_roles WHERE rolname='${role}'")" = "1" ]; then
    log "角色 ${role} 已存在，跳过创建（密码沿用 ${PW_FILE} 中记录）"
  else
    psql_admin "$port" postgres "CREATE ROLE ${role} LOGIN PASSWORD '${pw}'"
    log "角色 ${role} 已创建"
  fi
  if [ "$(psql_admin "$port" postgres "SELECT 1 FROM pg_database WHERE datname='${db}'")" = "1" ]; then
    log "数据库 ${db} 已存在，跳过创建"
  else
    psql_admin "$port" postgres "CREATE DATABASE ${db} OWNER ${role} ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' TEMPLATE template0"
    log "数据库 ${db} 已创建（owner=${role}；C locale 保证索引行为与演练环境一致）"
  fi

  # 2) pg_hba：备份 → 追加最小授权 → 重载
  local hba
  hba="$(psql_admin "$port" postgres "SHOW hba_file")"
  if ! grep -qE "^host[[:space:]]+${db}[[:space:]]+${role}[[:space:]]+${SERVER_A_IP}/32" "$hba"; then
    cp "$hba" "${hba}.bak.$(date +%Y%m%d%H%M%S)"
    printf 'host\t%s\t%s\t%s/32\tscram-sha-256\n' "$db" "$role" "$SERVER_A_IP" >> "$hba"
    log "pg_hba 已追加：仅 ${SERVER_A_IP} 可用 ${role} 访问 ${db}（备份在 ${hba}.bak.*）"
  else
    log "pg_hba 规则已存在，跳过"
  fi
  psql_admin "$port" postgres "SELECT pg_reload_conf()" >/dev/null

  # 3) 检查宽放规则（只报告，不自动删——现有条目可能有其他用途）
  local wide
  wide="$(grep -vE '^[[:space:]]*#' "$hba" | grep -nE '(0\.0\.0\.0/0|::/0)' || true)"
  if [ -n "$wide" ]; then
    log "⚠ ${label} 实例 pg_hba 存在对全网开放的条目（见下），建议人工收紧到内网网段/指定来源："
    echo "$wide" | sed 's/^/      /'
  fi

  local listen cfg
  listen="$(psql_admin "$port" postgres "SHOW listen_addresses")"
  cfg="$(psql_admin "$port" postgres "SHOW config_file")"
  if [ "$listen" = "localhost" ]; then
    log "⚠ ${label} 实例 listen_addresses=localhost，内网不可达。请修改并重启该实例："
    log "    sed -i \"s/^#*listen_addresses.*/listen_addresses = '*'\" ${cfg}"
    log "    （更稳妥：listen_addresses = '127.0.0.1,192.168.44.12' 只监听回环+内网网卡）"
    log "    然后: sudo systemctl restart <该实例的 postgresql 服务名>  （systemctl list-units | grep -i postgres）"
  else
    log "listen_addresses = ${listen} ✓"
  fi
}

PROD_PW="$(get_or_create_pw prod)"
TEST_PW="$(get_or_create_pw test)"

setup_instance "生产" "$PROD_PORT" "$PROD_ROLE" "$PROD_DB" "$PROD_PW"
setup_instance "测试" "$TEST_PORT" "$TEST_ROLE" "$TEST_DB" "$TEST_PW"

# 本机防火墙提示（放行命令由执行人决定）
if command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
  log "firewalld 在运行——若 A 机内网探测不通，执行："
  log "    firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=${SERVER_A_IP}/32 port port=5432 protocol=tcp accept'"
  log "    firewall-cmd --permanent --add-rich-rule='rule family=ipv4 source address=${SERVER_A_IP}/32 port port=15432 protocol=tcp accept'"
  log "    firewall-cmd --reload"
elif command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  log "ufw 在运行——确认放行内网端口：sudo ufw allow from ${SERVER_A_IP} to any port 5432,15432 proto tcp"
fi

cat <<EOF

════════════════════ 下一步：粘贴到服务器 A 的 .env ════════════════════════
TEST_DATABASE_URL=postgresql+asyncpg://aozora_test:${TEST_PW}@192.168.44.12:15432/aozora_test
PROD_DATABASE_URL=postgresql+asyncpg://aozora_prod:${PROD_PW}@192.168.44.12:5432/aozora_prod
════════════════════════════════════════════════════════════════════════════
（密码同步保存在 B 机 ${PW_FILE}，chmod 600——请一并纳入备份，丢失即需改密重配）

B 机验收（从 A 机执行，通了才继续 A 机部署）：
  nc -zvw3 192.168.44.12 15432   # 测试实例内网可达
  nc -zvw3 192.168.44.12 5432    # 生产实例内网可达
EOF
