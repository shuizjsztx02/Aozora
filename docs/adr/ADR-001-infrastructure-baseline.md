# ADR-001 · 基础设施现状盘点（T-M0-07）

- **状态：** 部分回填（2026-09-11）——双服务器与 PostgreSQL 实参已确认（见文末"回填记录"）；域名/证书/备案仍待落地，生产 HTTPS 链路挂起
- **决策日：** 2026-09-04
- **依赖：** T-M0-06 已完成

## 背景

T-M0-07 要求输出：无密码基础设施清单、staging 可用性结论、实际技术栈 ADR。
该任务的输入（服务器 OS、CPU/内存、网络、域名/证书、PostgreSQL 版本、备份策略、容器运行时、地域与合规）由账户所有者在外部控制，本仓库无权访问。

## 决策

不在仓库内虚构或模拟基础设施参数。将 T-M0-07 标注为"待外部确认"，以本 ADR 与风险登记册 `R-PROD-09` 作为占位记录，并在 `production-execution-plan.md` 中将其从 M0 完成项中分离。
生产部署或 staging 演练前（最晚 T-M6-01 前），必须由账户所有者提供真实参数并回填本 ADR。

## 临时替代方案

- T-M2-01/02 的开发与 CI 门禁使用 `docker-compose.yml` 中的 PostgreSQL 18 镜像与本地 SQLite 测试库，不依赖生产基础设施。
- 服务端模型与迁移（T-M2-02）在 SQLite 上通过 100% pytest 门禁；生产环境目标为 PostgreSQL 18，跨数据库差异由 `_YearMonth` 自定义 `@compiles` 与迁移注释说明。
- `D-EXEC-01` 与 `D-EXEC-02` 的具体参数保留在生产执行规划表"实施参数确认清单"中，最晚确认时点已标注。

## 未落地项

| 参数 | 最晚确认 | 当前状态 |
|---|---|---|
| D-EXEC-01：服务器、地域、网络、域名、证书、备案、staging | T-M6-01 前 | 待账户所有者提供 |
| D-EXEC-02：PostgreSQL 备份、异地副本、恢复责任 | T-M6-01 前 | 待账户所有者提供 |

## 风险

- `R-PROD-09` 已登记此缺口。
- 如账户所有者长期不确认，T-M6-01/02（生产发布）将被阻塞；不影响 M1–M5 的交付。

## 回填方式

账户所有者提供真实参数后，更新本 ADR 的"决策"段与"未落地项"表，并同步更新 `docs/risk-register.md` 关闭 `R-PROD-09`。

## 回填记录（2026-09-11，账户所有者提供）

| 参数 | 实值 | 状态 |
|---|---|---|
| 服务器 A（业务） | 公网 `116.62.196.255` / 内网 `192.168.44.13`，2C4G，Nginx 1.16.1 已装（当前未监听端口） | ✅ 确认 |
| 服务器 B（数据库） | 公网 `47.96.181.37` / 内网 `192.168.44.12`，2C4G，Docker PostgreSQL 18.6 双容器：`pg-prod`（5432 / `aozora_prod`）和 `pg-test`（15432 / `aozora_test`），数据目录分别为 `/data/postgresql/prod`、`/data/postgresql/test`；运维自建的 `prod_db` / `test_db` 原地保留不动 | ✅ 确认 |
| 部署形态 | A：Docker Compose 双实例（`docker-compose.prod.yml`，api-test 绑 `0.0.0.0:18080` 以兼容现有 Nginx 的 A 机内网地址上游、api-prod 绑 `127.0.0.1:8080`，经 Nginx 分流：测试 `80` + `/aozora-api/` 前缀、生产 `443`）；B：现有 Docker PostgreSQL 双容器，仅接受 A 内网访问（精确 `pg_hba` + 安全组双限） | ✅ 落地文件已入库 |
| 网络 | 同 VPC 内网 `192.168.44.0/24` 互通；云安全组由账户所有者配置（规则清单见 `infra/deploy/prod/RUNBOOK.md` §1） | ✅ 可放行（已确认） |
| 域名 / 证书 / 备案 | **仅有 IP，无域名**（阿里云大陆节点，80/443 域名访问需备案） | ⏳ 生产 HTTPS 挂起，T-M6-01a 待办 |
| 数据迁移 | 本地联调库不迁移；B 机新建业务库 `aozora_prod` / `aozora_test`（owner 为不可登录的归属角色 `aozora`），由 API 账号执行 `alembic upgrade head` 建表；原 `test_db` 中的测试数据按 `RUNBOOK.md` §4.1 以 pg_dump/pg_restore 迁入 | ✅ 确认 |
| 备份 | 云平台每日整机快照（A+B，非数据库级）；DB 级 pg_dump（ADR-005）待生产阶段在 B 机配置 | ⏳ D-EXEC-02 剩余项 |
| staging | 本地 docker-compose.staging.yml 保留为演练/演示环境；线上"测试环境"= 本文 api-test 实例 | ✅ 决策 |

`R-PROD-09` 保持开放（域名/备案/证书未闭环，阻塞生产发布而非测试链路）；
`R-PROD-12`（release 基址）与域名同批关闭。执行入口：`infra/deploy/prod/RUNBOOK.md`。

> 2026-09-11 端口边界补充（T-M6-01a）：`api-test` 的主机发布改为
> `0.0.0.0:18080`，使现有 Nginx 上游 `192.168.44.13:18080` 可连接；
> **云安全组不得为 TCP 18080 增加公网入方向规则**。对外访问仍必须经 Nginx
> 的受控入口；若发现 18080 已被公网放行，应先移除该规则再继续测试。

> 2026-09-17 库分离：业务库与运维自建的初始化库解耦——B 机两个实例各新建
> `aozora_prod` / `aozora_test`（owner 为 `NOLOGIN` 归属角色 `aozora`），不再直接
> 使用运维测试期创建的 `prod_db` / `test_db`。低权限 API 账号沿用
> `aozora_api_prod` / `aozora_api_test` 不变（已存在的账号复用原密码）；业务表
> owner 是执行 Alembic 建表的 API 账号本身，与库 owner 无关。原 `test_db` 的测试
> 数据经 pg_dump/pg_restore 迁入 `aozora_test`，旧库全程不改动，可随时回退。
> 建库、建角色、授权与 pg_hba 规则均由 `infra/deploy/prod/b-setup-postgres.sh`
> 幂等完成。

> 2026-09-18 Nginx 实地核对：A 机 nginx 为**源码编译**安装于 `/usr/local/nginx`
> （非 `/etc/nginx`），站点配置在 `/usr/local/nginx/conf.d/`（`api.conf` + `ups.conf`），
> 监听 **80**、路径前缀 **`/aozora-api/`**——该前缀由应用层
> `SharedProxyPrefixMiddleware`（`server/src/main.py`，提交 `dc05b1c`）剥离，
> nginx 原样透传。仓库原先设计的 8081 + `/etc/nginx/conf.d/` 方案**从未落地**
> （`/etc/nginx` 不存在），已按实况修正 `infra/deploy/prod/nginx/`（`api.conf` +
> `ups.conf` 副本）、`RUNBOOK.md`（§1/§5/§6/§8/§9）、`docker-compose.prod.yml` 注释
> 与客户端的 NSC 注释。实际形态、已知风险（`location /` 无 IP 限制、upstream 走
> DHCP 内网 IP、全站无访问日志）及一处磁盘/运行态漂移（`api.conf` 尾斜杠未 reload）
> 见 `infra/deploy/prod/nginx/DEPLOYED.md`。
