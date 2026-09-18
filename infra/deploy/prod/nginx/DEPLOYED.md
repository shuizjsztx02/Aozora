# A 机 Nginx 实际部署形态（2026-09-18 实地只读核对）

> ⚠️ **本目录的配置以 `api.conf` + `ups.conf` 为准**——这两份是服务器 A 上
> `/usr/local/nginx/conf.d/` 实际部署文件的仓库副本。`aozora-prod.conf.template`
> 是尚未落地的生产设计稿（域名/备案前置未满足）。
> 核对人仅做只读检查，未改动服务器上任何配置。

## 目录文件构成

| 文件 | 性质 |
|---|---|
| `api.conf` | **实际部署副本**——A 机 `/usr/local/nginx/conf.d/api.conf` 的等价内容 |
| `ups.conf` | **实际部署副本**——A 机 `/usr/local/nginx/conf.d/ups.conf` 的等价内容 |
| `aozora-prod.conf.template` | **未落地设计稿**——生产 443 + 域名入口，前置条件见 RUNBOOK 第 7 节 |
| `DEPLOYED.md` | 本文，实际形态说明 |

原 `aozora-test.conf`（8081 方案）已由 `api.conf` 取代：该方案从未在服务器上落地，
保留会误导（详见下方「演进过程」）。

## 实际形态

| 项 | 实际 |
|---|---|
| 安装方式 | **源码编译** `/usr/local/nginx`（1.16.1）；`/usr/bin/nginx` 是它的软链 |
| 服务托管 | `/etc/init.d/nginx`（LSB 脚本），已连续运行 |
| `/etc/nginx` | **不存在**——设计稿假设的 `/etc/nginx/conf.d/` 落地路径无效 |
| 主配置 | `/usr/local/nginx/conf/nginx.conf`（仅全局设置），站点配置经 `include /usr/local/nginx/conf.d/*.conf` 引入 |
| 站点配置 | `conf.d/api.conf`（server 块）+ `conf.d/ups.conf`（upstream 定义） |
| 监听端口 | **80**（不是设计稿设想的 8081） |
| 路径前缀 | **`/aozora-api/`**——见下方「服务端前缀剥离」 |
| upstream | `aozora-api-ups` → `192.168.44.13:18080`（A 机自身 eth0 内网 IP），`keepalive 500` |
| 完整链路 | 公网 `116.62.196.255:80/aozora-api/...` → 宿主机 `18080` → `aozora-api-test` 容器 |
| 实测验证 | `http://127.0.0.1/aozora-api/` 与 `http://127.0.0.1/aozora-api/docs` 均返回 200 |

## 服务端前缀剥离（关键约定）

`api.conf` 中 `proxy_pass http://aozora-api-ups` **不带尾斜杠**，即 nginx 把
`/aozora-api/` 前缀**原样透传**给上游，由应用剥离：

```
/aozora-api/api/v1/health  ──nginx 原样透传──▶  /aozora-api/api/v1/health
                           ──SharedProxyPrefixMiddleware 剥离──▶  /api/v1/health
```

- 实现：`server/src/main.py::SharedProxyPrefixMiddleware`（纯 ASGI，挂在最外层）
- 提交：`dc05b1c`「A 机 Nginx 为多业务共享，`location /aozora-api/` 无法改为剥离前缀」
- 用例：`server/tests/test_main.py::TestSharedProxyPrefix`（5 项）
- 不带前缀的请求（回环探活、容器 healthcheck、本机开发）原样透传，两形态共存
- 实测判据：`/aozora-api/aozora-api/api/v1/health` 返回 **404**（若 nginx 自行剥离
  则会剥成 `/api/v1/health` 返回 200）——证明前缀由应用而非 nginx 剥离

## ⚠ 磁盘配置与运行态不一致（2026-09-18 新发现）

服务器**磁盘上**的 `api.conf` 与**正在运行**的配置不是同一份：

| 证据 | 值 |
|---|---|
| nginx worker 进程启动 | 2026-09-11 **15:41:23** |
| 最后一次 reload | 2026-09-11 **15:41:24** |
| `api.conf` 文件 mtime | 2026-09-11 **15:50:12**（晚于 reload 9 分钟） |
| `ups.conf` 文件 mtime | 2026-09-11 15:41:20（在 reload 之前 ✅） |

磁盘上 `location /aozora-api/` 的 `proxy_pass` 当前写作
`http://aozora-api-ups/;`（**多一个尾斜杠**），语义是"由 nginx 剥离前缀"。该写法
写入于 15:50:12，之后**没有 reload 过**（error.log 中最后一条 `signal process started`
停在 15:41:24），所以**尚未生效**——线上实测行为仍是原样透传。

**影响**：任何人执行一次 `nginx -s reload` 或重启，语义就会从"应用剥离"漂移为
"nginx 剥离"。此时单前缀请求仍能通（剥离后直接命中路由），但
`SharedProxyPrefixMiddleware` 沦为死代码，且与 `main.py` 的设计假设相反。

**处置建议**：下次 reload 前，先把服务器上那一行的尾斜杠去掉，回到本目录
`api.conf` 的形态。

## 演进过程（error.log 印证）

9/11 15:03 尝试部署设计稿失败（`/var/log/nginx/aozora-test.access.log` 目录不存在）；
15:38 两次 reload 失败（一次 `api.conf` 缺 `}`，一次 `host not found in upstream`）；
15:41 建 `ups.conf` 后 reload 成功；15:42 出现 `connect() failed` 到 upstream，
是容器尚未监听 18080；15:50 给 `proxy_pass` 加了尾斜杠（未 reload）；16:42 提交
`dc05b1c` 改为应用层剥离。之后链路稳定。

## 已知风险点（只读检查发现，未改动）

1. **`location /` 实际不限来源**——`allow` 列表**是生效的（未注释）**，但其后的 `deny all` 处于注释状态；nginx 的 allow/deny 按顺序匹配、**无匹配即放行**，因此这组 allow 不产生任何限制。加之该 location 没有 `proxy_pass`，请求落到默认静态根 `/usr/local/nginx/html`，实测返回 200 的 nginx 默认页。`location /aozora-api/` 同样无 IP 限制，**公网可达性完全取决于云安全组**。
2. **upstream 用 eth0 内网 IP（`192.168.44.13`）而非 `127.0.0.1`**——该网卡由 DHCP 动态分配，IP 变更时 upstream 会静默失联。走回环地址更稳。
3. **访问日志全局关闭**——`http` 块中 `access_log off`，`api.conf` 里的 `access_log` 也是注释状态，`log_format main` 定义了却无人引用。出问题时没有访问日志可查。
4. **`client_max_body_size` 实际是 `1024m`**（继承自 `http` 块），不是设计稿写的 `12m`——上传体积目前完全靠应用层 `MAX_UPLOAD_BYTES` 兜底。
5. **路径前缀是易错点**——9/17 日志中出现过 `GET /api/v1/health/ready` 落到静态路径的记录，原因可能是把服务端的 `API_PREFIX` 误当成外部入口前缀（外部入口必须带 `/aozora-api/`）。
6. **reload 前须先消除上文「磁盘配置与运行态不一致」**，否则行为会静默漂移。

## 对 M7 P1（SSE）的影响

**结论方向不变，但配置载体不同**：实际配置同样**没有** `proxy_buffering off`，nginx 默认 `proxy_buffering on` 会把 SSE 事件攒批下发，中间步骤实时性归零。要改的是 **`/usr/local/nginx/conf.d/api.conf`**，**不是本目录下的生产模板**。

另外三点差异：

- 实际已有 `proxy_http_version 1.1` + `proxy_set_header Connection ""`（keepalive 配置），对 SSE 有利，应保留。
- **没有设置 `proxy_read_timeout`**，用 nginx 默认 60 s——服务端仍必须有 heartbeat，否则静默即断。
- `access_log off` 会让 SSE 的时延与断连问题难以排查，P1 联调期间建议临时开启访问日志。

> **动手前的前提：** 需先确认云安全组对公网 **80 端口**的放行范围——它决定 `location /` 敞开的实际暴露面，也决定 `/aozora-api/` 是否真的对全网可达。

## 待决事项

- [x] 将本目录的设计稿更新为与实际一致的形态 —— 已由 `api.conf` + `ups.conf` 副本承载（2026-09-18）
- [ ] 消除服务器磁盘 `api.conf` 的尾斜杠漂移（见「磁盘配置与运行态不一致」）
- [ ] 是否修复上述风险点（尤其 `location /` 敞开、upstream 走 DHCP IP、无访问日志）
- [x] `RUNBOOK.md` 中按 `/etc/nginx` + 8081 描述的步骤需相应修正 —— 2026-09-18 已修正