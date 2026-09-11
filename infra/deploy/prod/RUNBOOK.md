# Aozora 生产迁移 RUNBOOK — 双服务器部署（T-M6-01a 实操版）

> 2026-09-11 起生效。对应 `docs/milestones/production-execution-plan.md` T-M6-01a
> 与 `docs/adr/ADR-001`（已回填真实参数）。执行方式：**本仓库只备配置与脚本，
> 命令由执行人在服务器上运行**，输出/报错回传诊断。

## 0. 拓扑与本轮范围

```
公网用户 ──HTTPS:443──┐                       ┌── 内网 TCP 5432 ──▶ B机 Docker pg-prod / prod_db
                      ▼                       ▼
测试人员 ──IP:8081──▶ A机 Nginx ──▶ api-test/api-prod（容器，只绑 127.0.0.1:18080/8080）
                       (192.168.44.13)              │
                                                    └── 内网 TCP 15432 ──▶ B机 Docker pg-test / test_db
                                                    (47.96.181.37 / 192.168.44.12)
```

| 机器 | 角色 | 规格 | 跑什么 |
|---|---|---|---|
| A `116.62.196.255` / 内网 `192.168.44.13` | 业务 | 2C4G，Nginx 1.16.1 已装 | Docker：`api-test`（:18080）、`api-prod`（:8080，本轮不启动） |
| B `47.96.181.37` / 内网 `192.168.44.12` | 数据库 | 2C4G，Docker PG 18.6 双容器已装 | `pg-prod`：5432 / `prod_db`；`pg-test`：15432 / `test_db`；仅接受 A 机内网访问 |

**本轮范围 = 测试链路**（截图规划：测试人员 → `http://116.62.196.255:8081`）。
选择理由：① 8081 非标端口按 IP 访问，与 ICP 备案管控（域名+80/443）无关，现在就能跑通；
② 生产实例 `ENV=production` 被服务端校验器硬性要求 CORS 为 `https://*.aozora.app` 格式
（`server/src/common/config.py::_check_production_safety`）——没域名就没法合规启动，
第 7 节作为待办挂起。
③ 测试实例跑 `ENV=staging + DEV_AUTH_CODE=000000`，短信供应商（D-EXEC-03）未接入前
**只有 staging 能测手机验证码登录**，这正是测试环境存在的意义。

**数据策略**：本地联调库**不迁移**。B 机保留现有初始化库 `prod_db` / `test_db` 与
数据卷；部署脚本仅创建低权限 API 账号并收紧 HBA，由 A 机 `alembic upgrade head`
建表（当前链至 `0015_feedback`+）。

observability 栈（Prometheus/Grafana/Loki/Tempo/OTel/Alertmanager 六件套体检台）
**本轮不上**：2C4G 与 API 同挤会互相拖累，测试期 `docker logs` + 直查 B 库足够。
代码侧埋点已就绪，将来设 `ENABLE_TELEMETRY=true` 并加 collector 即接通（第 7.6 节）。

> ⚠ 执行前置：本 RUNBOOK 与配套文件目前在你 Windows 工作区。开跑前先
> 按 §3.2 的精确路径暂存并 `git commit && git push`（或整目录 scp 到 A 机），否则 A 机
> `git clone` 拿不到这些脚本。见 §3.2。

## 1. 云安全组（控制台操作，5 分钟）

| 机器 | 方向 | 协议/端口 | 来源 | 用途 |
|---|---|---|---|---|
| A | 公网入网 | TCP `8081` | `0.0.0.0/0` | 测试入口（本轮唯一公网端口） |
| A | 公网入网 | TCP `22` | 你的办公 IP/32 | SSH |
| B | **仅内网**入网 | TCP `15432` | `192.168.44.13/32` | A→B 测试库 |
| B | **仅内网**入网 | TCP `5432` | `192.168.44.13/32` | A→B 生产库（本轮先放行，生产实例后续启用） |

- **绝对不要**给 B 的 5432/15432 开 `0.0.0.0/0`（公网侧）。整机快照兜底 ≠ 不怕泄露。
- 阿里云同 VPC 内网互访走内网安全组规则；若两台机器在同一安全组，也可用
  "安全组内互通"替代上面两条，但端口仍建议在组方向上最小化。

## 2. 服务器摸底（A、B 都执行，输出留存）

```bash
cat /etc/os-release          # 发行版决定 §3.1 走哪个安装分支
nproc && free -h && df -h /  # 确认 2C4G 与磁盘
timedatectl                  # NTP 必须 active（JWT 过期、AI 日配额按本地午夜重置都依赖时钟）
```

## 3. 服务器 A：业务容器

### 3.1 安装 Docker（若无；按发行版二选一）

```bash
docker --version && docker compose version   # 先探测；compose 必须是 V2（带空格子命令）

# —— Alibaba Cloud Linux 3 / CentOS 系 ——
sudo yum install -y yum-utils
sudo yum-config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# —— Ubuntu / Debian 系 ——
for f in /etc/apt/sources.list.d/docker.list; do
  sudo rm -f $f
done
sudo apt-get update && sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://mirrors.aliyun.com/docker-ce/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | sudo tee /etc/apt/sources.list.d/docker.list
sudo apt-get update && sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# —— 通用（Debian 系）——
sudo systemctl enable --now docker
sudo usermod -aG docker $USER && newgrp docker   # 免 sudo 用 docker（可选）
```

### 3.2 获取仓库

```bash
# 方式一（默认，需要先把本 RUNBOOK 所在提交 push 到 origin）：
git add .dockerignore docker-compose.prod.yml server/Dockerfile \
  app/src/debug/res/xml/network_security_config.xml \
  docs/adr/ADR-001-infrastructure-baseline.md infra/README.md infra/deploy/prod
git diff --cached --check && git diff --cached --name-status
git commit -m "feat(infra): add dual-server staging deployment assets (T-M6-01a)"
git push origin main
git clone https://github.com/shuizjsztx02/Aozora.git ~/Aozora && cd ~/Aozora
# 后续更新：cd ~/Aozora && git pull --ff-only

# 方式二（不 push 时，Windows PowerShell 直接传工作区，排除杂项）：
#   scp -r D:\Code2026\aozora\{docker-compose.prod.yml,server,contracts,infra,app,.github,.gitignore} ubuntu@116.62.196.255:~/Aozora/
#   （服务端镜像构建只用到 server/ + contracts/；app/ 只为 Alembic 之外的完整性）
cd ~/Aozora
```

### 3.3 配置 `.env`

```bash
cp infra/deploy/prod/deploy.env.example .env
openssl rand -hex 32    # 跑两次，分别填 TEST_SECRET_KEY / PROD_SECRET_KEY
vim .env
# TEST_DATABASE_URL ← 第 4 节 B 机脚本末尾打印的那行，原样粘贴
# AI_PROVIDER_API_KEY ← 从本地 Windows 的 server/.env 里复制同一个 DashScope key
```

### 3.4 部署测试实例

```bash
./infra/deploy/prod/a-deploy.sh test
```

脚本自动做：前置检查（docker/.env 完整性/A→B 内网 TCP 连通）→ 构建镜像 →
起 `aozora-api-test`（只绑 `127.0.0.1:18080`）→ 容器内 `alembic upgrade head` →
`/health/ready` 探活。失败即停并打印排查方向；看日志：
`docker compose -f docker-compose.prod.yml --profile test logs --tail 50 api-test`。

## 4. 服务器 B：配置 API 账号与 HBA（一次性，约 5 分钟）

把仓库带上 B 机（任意方式，脚本自包含）后：

```bash
git clone https://github.com/shuizjsztx02/Aozora.git ~/Aozora && cd ~/Aozora
bash infra/deploy/prod/b-setup-postgres.sh
```

脚本只操作已存在的 Docker 容器 `pg-prod` / `pg-test`：保留 `prod_db` / `test_db`
和数据卷，创建低权限 `aozora_api_prod` / `aozora_api_test`，授予 Alembic 所需的
数据库与 `public` schema 权限。它会先备份容器内 `pg_hba.conf`，再把 initdb 默认的
`host all all all ...` 宽规则替换为仅 `192.168.44.13/32` 可访问对应库与账号的规则，
reload 后做规则校验。脚本不读取管理员密码文件；API 密码存
`/root/aozora-app-db-passwords.txt`（权限 600），并在末尾打印两条 `DATABASE_URL`。

若 API 账号已经存在而密码文件缺失，脚本会停止而不是重置未知密码；此时先回传错误
输出，禁止手动删除角色或密码文件。

**验收（A 机执行）**：

```bash
nc -zvw3 192.168.44.12 15432   # succeeded 才算 B 机侧完成
nc -zvw3 192.168.44.12 5432
```

## 5. 服务器 A：Nginx 测试入口

```bash
sudo cp infra/deploy/prod/nginx/aozora-test.conf /etc/nginx/conf.d/
sudo nginx -t && sudo systemctl reload nginx
```

两个常见拦路虎（RHEL/Alibaba Cloud Linux 系）：

```bash
# ① SELinux 拒绝 nginx 反连本机 18080 → 表现为 502：
getenforce                          # Enforcing 才需要
sudo setsebool -P httpd_can_network_connect 1

# ② 系统自带 firewalld 挡 8081（安全组之外的第二道门）：
sudo firewall-cmd --state && sudo firewall-cmd --permanent --add-port=8081/tcp && sudo firewall-cmd --reload
```

## 6. 端到端验收

### 6.1 公网冒烟（Windows PowerShell）

```powershell
curl.exe http://116.62.196.255:8081/api/v1/health
curl.exe http://116.62.196.255:8081/api/v1/health/ready
curl.exe -X POST http://116.62.196.255:8081/api/v1/auth/guest `
  -H "Content-Type: application/json" -d '{\"device_id\":\"smoke-win\",\"timezone\":\"Asia/Shanghai\"}'
# 期望：两个 200 JSON；guest 返回 access_token/refresh_token/user_id
```

### 6.2 测试 APK（本机 Windows 出包）

NSC debug 覆盖层已加入 `116.62.196.255` 明文白名单（本轮仓库改动），release
构建不受影响。出包并分发：

```powershell
.\gradlew :app:assembleDebug "-PAOZORA_DEBUG_API_BASE=http://116.62.196.255:8081/api/v1"
# 产物 app\build\outputs\apk\debug\app-debug.apk —— adb install 或发给测试人员
```

### 6.3 业务闭环走查

1. 首启同意隐私 → 游客注册 → 快速记账（本地 NLU 路径）→ 等待自动同步；
2. 手机验证码登录可测（staging `DEV_AUTH_CODE=000000`）；
3. `.env` 里配了 AI key → AI 记账走 DashScope；没配 → 503 自动降级本地 NLU，均算通过；
4. **落库断言**（B 机）：

```bash
docker exec pg-test sh -c \
  'psql -U "$POSTGRES_USER" -d test_db -c "select count(*) from transactions; select count(*) from sync_changes;"'
```

5. 断网记账 → 恢复 → pending 上行、对账一致（沿用 T15 联调走查项）。

### 6.4 环境重置（测试库随手清零）

```bash
# B 机：这是破坏性操作，仅清空测试库；生产库与数据卷不受影响。
docker exec pg-test sh -c \
  'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE test_db WITH (FORCE);" -c "CREATE DATABASE test_db OWNER test_admin TEMPLATE template0 ENCODING '\''UTF8'\'' LC_COLLATE '\''C'\'' LC_CTYPE '\''C'\'';"'
# B 机：重新授权既有 API 账号并确认 HBA，输出与密码均不应变化。
bash infra/deploy/prod/b-setup-postgres.sh
# A 机：再跑一次 ./infra/deploy/prod/a-deploy.sh test（会重放 alembic）
```

## 7. 生产链路待办（域名落地后执行）

1. **域名 + ICP 备案**：大陆节点硬前提（未备案域名解析到 80/443 会被云厂商拦截）。
   申请 `api.aozora.app` → A 机，备案通过。
2. **证书**：`sudo yum install certbot python3-certbot-nginx`（或 apt 对应）→
   `sudo certbot --nginx -d api.aozora.app`；或阿里云免费 DV 证书 + 手动部署。
   自动续期 `systemctl list-timers | grep certbot` 确认。
3. **Nginx 生产入口**：取消
   `infra/deploy/prod/nginx/aozora-prod.conf.template` 内注释、填证书路径 →
   拷入 `conf.d/` → `nginx -t && reload`；安全组放行 A 机 80/443。
4. **`.env` 生产段**：`PROD_DATABASE_URL`（§4 已打印）、`PROD_SECRET_KEY`
   （独立随机）、`PROD_CORS_ORIGINS=https://api.aozora.app`。
   注意 CORS 校验器只认 `https://[子域.]aozora.app` 格式——若前端 WebView 之外无
   浏览器方调用，列表里放这一个域名即可。
5. **上线前必须恢复的代码项**：`server/src/common/config.py` 的
   `ai_daily_quota_guest` 默认值仍是联测临时提额 **100000**（commit bb07875 注明
   "联测结束恢复 20"）。compose 已把生产实例显式钉为 `PROD_AI_QUOTA:-20`，
   但默认值本身要改回去，防止其他环境漂移。
6. **SMS 供应商（D-EXEC-03）未接入**：`ENV=production` 拒绝 `DEV_AUTH_CODE`，
   手机验证码登录在生产不可用。上线决策二选一：(a) 仅游客先行（产品可接受？），
   (b) 接入短信供应商后再开生产实例。
7. **数据库备份（D-EXEC-02）**：云平台每日整机快照 ≠ 数据库级备份（无法单库恢复/
   异地留存）。在 B 机配置 `infra/backup/backup.sh`（pg_dump -Fc，ADR-005）
   每日 cron + `verify-backup.sh` 校验，异地副本与保留天数按
   `infra/backup/production-params.md` 清单回填。**整机快照只作最后一道兜底。**
8. **附件/导出持久性**：容器卷 `api-prod-data:/data` 已兜底（重启不丢），
   但对象存储（D-EXEC-05）未接入前，磁盘故障即丢文件——上线评审时明示此风险。
9. **observability**：届时决策（轻量 = A 机加 prometheus+grafana 两容器，
   `ENABLE_TELEMETRY=true`；完整 = staging 全套 6 件套，建议挪第三台机或 B 机）。
10. **release 客户端**：`-PAOZORA_API_BASE=https://api.aozora.app/api/v1` +
    keystore 签名（R-PROD-12 在此关闭）。
11. **文档回填**：本文件 §7 完成一项勾一项；`docs/adr/ADR-001` 与
    `docs/risk-register.md` R-PROD-09/R-PROD-12 同步收口。

## 8. 回滚 / 下线

```bash
# 测试实例下线（数据卷保留）：
docker compose -f docker-compose.prod.yml --profile test down
# 连数据卷清除（测试库上传件）：加 -v；数据库本身在 B 机，不受影响
# 版本回滚：cd ~/Aozora && git checkout <上个可用 tag/commit> && ./infra/deploy/prod/a-deploy.sh test
# nginx：sudo rm /etc/nginx/conf.d/aozora-test.conf && sudo nginx -t && sudo systemctl reload nginx
```

## 9. 故障排查速查

| 症状 | 大概率原因 | 处置 |
|---|---|---|
| a-deploy.sh 报"A→B 内网不通" | 安全组没放行 / listen_addresses=localhost / B 机防火墙 | §1、§4 脚本报告、§4 firewalld 提示 |
| health/ready 503、日志 asyncpg 拒绝 | pg_hba 没有 A 的行 / 密码不符（以 `/root/aozora-db-passwords.txt` 为准） | B 机重跑 §4 脚本（幂等）后 `docker compose ... restart api-test` |
| alembic 报 `No 'script_location'` | 镜像是旧版（未含 alembic.ini 的 Dockerfile 改动） | 确认 §3.2 拿到的提交包含本轮改动，`up -d --build` 重建 |
| 公网 curl 8081 不通、A 机本机 curl 18080 通 | 安全组 / nginx 未 reload / SELinux | §5 两条、`sudo ss -lntp \| grep 8081` |
| 8081 返回 502 | SELinux `httpd_can_network_connect`（RHEL 系默认拦） | §5-① |
| 测试 APK 报 CLEARTEXT not permitted | 装的是旧包（NSC 未含服务器 IP）或 release 包 | 重新 `assembleDebug` 出包安装 |
| AI 全 503 | `.env` 未填 `AI_PROVIDER_API_KEY` | 补 key 后重跑 a-deploy.sh test；或接受本地 NLU 降级 |
| 游客连测第 21 句 429 | 生产配额语义（测试已设 100000，若见 20 说明 .env 覆盖没生效） | 检查 `.env` `TEST_AI_QUOTA` 与容器内 `docker compose exec api-test printenv AI_DAILY_QUOTA_GUEST` |
