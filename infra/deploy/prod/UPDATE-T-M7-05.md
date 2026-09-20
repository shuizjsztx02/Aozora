# 服务端代码 → A / B 机更新方案（T-M7-05 收口轮）

**建立：** 2026-09-20
**范围：** 把 T-M7-01～T-M7-05 的服务端交付（当前 `origin/main`）更新到 A 机测试实例
`api-test`，并核对 B 机数据库侧影响。
**与既有文档的关系：** 本文件是**本次更新的操作方案**；拓扑、安全组、首次部署、
nginx 形态仍以 `RUNBOOK.md` 与 `nginx/DEPLOYED.md` 为准，本文件不重复它们，
只在需要时引用小节号。

---

## 一、现状核对（2026-09-20 只读实查）

| 项 | A 机 `116.62.196.255` / `192.168.44.13` | B 机 `47.96.181.37` / `192.168.44.12` |
|---|---|---|
| 代码 | `/root/Aozora`，HEAD **`42913ec`**，工作区干净，上游 `origin/main` | 无代码（纯数据库） |
| 裸库 | `/root/git/aozora.git`（本仓库 `a-deploy` remote 的推送目标） | — |
| 容器 | `aozora-api-test` **Up 2 天 (healthy)**，`0.0.0.0:18080→8000` | `pg-test` 15432（healthy，8 天）、`pg-prod` 5432（healthy，2 周） |
| nginx | `/usr/local/nginx/conf.d/{api,ups}.conf`，监听 80，前缀 `/aozora-api/` | — |
| 数据库迁移 | — | `aozora_test` = **`0016_cloud_entities`**；`aozora_prod` = 本轮不动 |

**结论：** A 机代码落后 `origin/main` 约 **40+ 个提交**（停在 T16-7 时代），
`aozora_test` 落后 **5 个迁移**。B 机不需要部署代码，但需要承接 A 机容器发起的迁移。

### 1.1 待应用的迁移（`0016` → `0021`）

| 迁移 | 内容 | 风险 |
|---|---|---|
| `0017_conversation_events` | T-M7-02f 会话事件表 + `ai_usage.conversation_id` | 纯新增 |
| `0018_conversation_event_turns` | 会话事件表加 `turn` 列 | 纯新增列 |
| `0019_conversation_index` | `ai_conversations` 表 + 列表索引 | 纯新增 |
| `0020_conversation_event_log` | 事件日志加 `time` / `ignorable` / `type` | 纯新增列 |
| `0021_user_preferences` | T-M7-05j `user_preferences` 表 | 纯新增 |

五个都是**纯新增**（建表 / 加列 / 加索引），不重写既有数据。

### 1.2 本次一并修复的部署缺口

更新前先落地两个**在规划里不可能生效**的缺陷（已在本轮提交）：

1. **`docker-compose.prod.yml` 完全没有接线 AI Agent 的环境变量**（`fix(infra)`，`0fc2d38`）
   —— 原先一个 `AI_AGENT_*` 都没写，容器里恒为 `config.py` 默认值，
   `AI_AGENT_ENABLED` 永远是 false、`/ai/agent` 恒 404。
   **不修这一条，「部署到 A 机后真机测 AI Agent」在配置层面就不可能发生。**
2. **`ai_daily_quota_guest` 代码默认值恢复为 20**（`chore`，`320d9c9`）
   —— 2026-09-11 联测临时提额留下的挂账，登记为「M7 收口统一恢复」。
   两个部署实例都由 compose 显式接管（test 走 `TEST_AI_QUOTA` 默认 100000、
   prod 走 `PROD_AI_QUOTA` 默认 20），故**不影响联测**。

---

## 二、更新步骤（测试实例）

> 全部命令由**执行人**在 A 机上运行。本仓库只备代码与脚本。

### 步骤 0：前置——B 机数据库转储（**必做**）

`docs/risk-register.md` 与既有记录都写着**数据库级备份尚未建立**
（`infra/backup/` 的调度 overlay 没有部署到 A 机）。本次要跑 5 个迁移，
而在没有备份的前提下跑迁移是不可接受的；`pg_dump` 一次性转储是最小代价的兜底。

在 **B 机**执行（密码从 A 机 `.env` 的 `TEST_DATABASE_URL` 里取，
形如 `postgresql+asyncpg://aozora_api_test:<password>@192.168.44.12:15432/aozora_test`）：

```bash
# 在 B 机上；把 <password> 换成上面解析出来的那一段
docker exec -e PGPASSWORD='<password>' pg-test \
  pg_dump -U aozora_api_test -d aozora_test -Fc \
  > /root/aozora_test_$(date +%Y%m%d_%H%M).dump

ls -lh /root/aozora_test_*.dump      # 确认非空、体积合理
```

> 直接 `docker exec pg-test pg_dump -U aozora_api_test ...` 会卡在交互式密码提示上，
> 而该用户不是 trust 认证；`-e PGPASSWORD` 是这条命令能跑通的关键。
> 自建库的归属角色 `aozora` 是 NOLOGIN，**不能**用它转储。
>
> 恢复演练：`docker exec -i -e PGPASSWORD='<password>' pg-test pg_restore -U aozora_api_test -d aozora_test --clean --if-exists < 转储文件`

### 步骤 1：本地（Windows）推送代码到 A 机裸库

```bash
git push a-deploy main
```

### 步骤 2：A 机更新工作库

```bash
cd /root/Aozora
git pull origin main
git log --oneline -1        # 确认 HEAD 已是本次目标提交
```

### 步骤 3：A 机 `.env` 补 agent 凭据

`.env` 在 A 机是 `-rw------- root`，**不进仓库**。本次需要新增一行
（密钥从本地 `server/.env` 的 `AI_AGENT_PROVIDER_API_KEY` 取）：

```bash
# 追加到 /root/Aozora/.env
AI_AGENT_PROVIDER_API_KEY=<DeepSeek Key>
```

`AI_AGENT_PROVIDER_MODEL` / `AI_AGENT_PROVIDER_BASE_URL` 有默认值
（`deepseek-flash` / `https://api.deepseek.com`），一般不用写。
`AI_AGENT_ENABLED` / `AI_DEVICE_CHANNEL_ENABLED` **不用写**——
compose 的 `api-test` 已经默认成 `true`（`TEST_AI_AGENT_ENABLED` /
`TEST_AI_DEVICE_CHANNEL_ENABLED`）。

### 步骤 4：部署（构建 + 迁移 + 探活）

```bash
cd /root/Aozora
./infra/deploy/prod/a-deploy.sh test
```

该脚本依次做：前置检查（docker / `.env` 必填项 / A→B 内网连通）
→ `docker compose --profile test up -d --build`
→ `alembic upgrade head`（**在容器内对 B 机的 `aozora_test` 执行**）
→ 轮询 `/api/v1/health/ready`（最多 60 s）。

### 步骤 5：验证

```bash
# 5.1 迁移到位
docker exec aozora-api-test alembic current      # 期望 0021_user_preferences (head)

# 5.2 健康
curl -s http://127.0.0.1:18080/api/v1/health/ready

# 5.3 agent 路径已开（本轮最关键的验证）
#     未开启时是 404；开启后应返回鉴权类错误（401/403），而不是 404
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  http://127.0.0.1:18080/api/v1/ai/agent -H 'Content-Type: application/json' -d '{}'

# 5.4 经 nginx 的公网入口（R-PROD-22 第 9 项的端到端确认）
curl -s http://116.62.196.255/aozora-api/api/v1/health/ready
```

### 步骤 6：B 机无需操作

B 机只跑 PG；迁移由 A 机容器发起。**不要**在 B 机上 `git pull` 或构建镜像。

---

## 三、回滚

| 层 | 手段 |
|---|---|
| 应用 | A 机 `git checkout <上一个 commit>` 后重跑 `a-deploy.sh test`（`--build` 会重建镜像） |
| 迁移 | `docker exec aozora-api-test alembic downgrade 0016_cloud_entities`；五个迁移都是纯新增，但**降级会丢新表数据**（测试库可接受） |
| 数据 | 用 §二 步骤 0 的 `pg_dump` 转储恢复 |
| 闸门 | 把 `.env` 里 `TEST_AI_AGENT_ENABLED=false` / `TEST_AI_DEVICE_CHANNEL_ENABLED=false` 写死再重建，即可退回「agent 路径 404」的旧行为 |

**回滚链的设计意图（架构 §11.1）：** agent 路径的两层闸门——服务端
`AI_AGENT_ENABLED`、客户端 `AI_AGENT_CLIENT_ENABLED`——任一关闭即整条链路不可达，
且关闭后行为与 M7 之前逐字相同。

---

## 四、生产实例（`api-prod`）——**本轮不动**

`api-prod` 的前置条件未满足，维持停用：`ENV=production` 被
`config.py::_check_production_safety` 硬性要求 CORS 为 `https://*.aozora.app`
格式，域名/备案未落地即拒绝启动（RUNBOOK 第 7 节）。因此本轮只更新测试实例；
`api-prod` 的镜像会随同一次 `--build` 一起构建，但 `--profile prod` 不启用即不启动。

---

## 五、已知风险与承接项

| # | 项 | 处置 |
|---|---|---|
| 1 | 数据库级备份未建立（`infra/backup/` overlay 未部署） | §二 步骤 0 用一次性 `pg_dump` 兜底；建立周期性备份仍是**未闭环风险**（见风险登记册） |
| 2 | `AI_DEVICE_CHANNEL_ENABLED` 开启后，设备工具的挂起态是**进程内单例**（R-PROD-34） | 测试期单 worker，可接受；进程重启会丢挂起请求（收敛为超时） |
| 3 | 域路由词表覆盖度无采集方（R-PROD-39） | 测试期观察 `phase: routing` 帧的 `matched` 字段 |
| 4 | 会话事件保留期任务（`AI_CONVERSATION_RETENTION_ENABLED=true`）是**进程内**任务，不挂 telemetry、不用 cron | 单 worker 下按轮触发；多 worker 前需重新设计（R-PROD-25 同源） |
| 5 | SSE 经真实 nginx 的端到端确认（R-PROD-22 第 9 项） | §二 步骤 5.4 覆盖 health；**agent 流式**的 nginx 端到端仍需在真机走查时确认 |

---

## 六、本轮之后仍待办（不在本方案范围）

- 周期性数据库备份落地的排期（`infra/backup/` overlay 上 A 机）。
- `api-prod` 启用所需的域名 / 备案 / HTTPS 证书（RUNBOOK 第 7 节）。
- 迁移 `0021` 之后的**旧客户端兼容性**：本轮服务端改动了 bootstrap checksum
  车道（新增 `checksum_v3`），客户端侧要跟上（见提交 `ab243a2` 与收口记录）。