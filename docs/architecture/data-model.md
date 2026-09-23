# Aozora · 数据模型约束

- 金额以正整数 `amountCents` 存储；收支方向由 `type` 表达。
- 登录用户的服务端数据是权威；Room 仅保存本地副本和待同步操作。
- 每个写操作携带 `mutationId`，服务端必须幂等处理。
- 可修改实体带 `version`，冲突必须显式返回，禁止静默覆盖。
- 交易同时保存 UTC `occurredAt` 和用户业务日期 `bizDate`。
- 统计口径：`MonthlyStats.balance = totalIncome − totalExpense`。允许负值表示赤字（支出 > 收入）。`MoneyCents` 存储层仍禁止负值，赤字通过 balance 计算结果表达而非存储。客户端统计、服务端统计与导出必须使用同一口径，保证 APP-CONTEXT §5.2 对账不变量（客户端明细合计 = 客户端统计 = 服务端统计 = 导出文件合计）。
- 空预算语义：`Budget.categoryId` 与 `Category.bookId` 以显式 `null`（JSON 标准表达）或字段缺省表示”整体预算 / 系统分类”，客户端类型须接受 `string | null | undefined` 三种形态。

## attachments 表（T-M4-04c）

附件上传存储receipt截图等用户文件。行级所有权由 `user_id` 强制，无需经由 transaction → book → user 链路。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | UUID PK | 附件唯一标识 |
| `user_id` | UUID FK → users.id NOT NULL | 上传者；上传时即强制，PENDING 阶段无需 transaction 即可做权限检查 |
| `transaction_id` | UUID FK → transactions.id NULL | 关联交易；链接前为 NULL |
| `object_key` | VARCHAR(512) NOT NULL | 存储路径 `<user_id>/<attachment_id>.<ext>`，兼容 S3 |
| `mime_type` | VARCHAR(128) NOT NULL | 以 magic-byte 嗅探结果为准（非 Content-Type header） |
| `file_size` | BIGINT NULL | 字节数，用于存储统计和配额 |
| `status` | VARCHAR(20) NOT NULL | `pending` / `linked` / `parsed` / `expired` |
| `expires_at` | TIMESTAMPTZ NULL | PENDING 24h 自动过期；LINKED 后清空 |

**状态机**：上传即 `PENDING`（24h TTL）→ 链接交易后 `LINKED`（永久保留）→ 未来 OCR 解析后 `PARSED`。超期未链接则惰性翻转为 `EXPIRED` 并删除物理文件。启动清理（`cleanup_expired_files`）仅针对 DB 中已过期的 `PENDING`/`EXPIRED` 行，绝不触碰 `LINKED`/`PARSED` 引用的文件（R-M4-39）。

## Room schema v2（T-M3-02 同步引擎）

- **`sync_meta(key TEXT PK, value TEXT NOT NULL)`**：同步元数据键值表，现用键 `sync_cursor`（已合并到的服务端 cursor）。cursor 更新与实体合并必须在同一事务内。
- **`pending_mutations` 新增列**（`MIGRATION_1_2`，旧行默认 `NULL`）：
  - `expectedVersion INTEGER`：update/delete/archive 入队时捕获的变更前实体版本号，推送时作为服务端 `version` 期望值；insert 为 `NULL`。
  - `lastError TEXT`：失败原因（冲突、非重试错误等）。
- **`failed` 状态语义**：版本冲突与不可重试错误（422/404）为终态，保留本地待处理版本等待用户处理，自动同步不再重试该 mutation；瞬时错误保持 `pending` 由 WorkManager 退避重试。
- **同步合并豁免**：服务端数据合并仅经 Repository server-merge 路径（`upsertFromSync`，REPLACE 语义）且不产生新 mutation；业务写入 insert 保持 ABORT 语义不变。
- **wire 契约映射**：Room/Bridge 层 camelCase，服务端同步/业务 API 为 snake_case；`occurredAt`（UTC ms）↔ ISO-8601 UTC 字符串；客户端 `archive` ↔ wire `delete`。

完整实体和同步规则以 `docs/APP-CONTEXT.md` 第 5 节为准。

## accounts 表：账户分类与当前余额（T18-5 / REQ-69，ADR-013）

第十八轮把两列**两端化**（此前只存在于客户端 Room：`accountType` 上行被 sync 静默丢弃、pull 又被冲刷成 NULL；余额则完全没有）：

| 列 | 类型 | 说明 |
|---|---|---|
| `account_type` | VARCHAR(64) NULL | 账户分类（自由文本：「储蓄卡」「微信」…），与客户端 AccountsPage 同值域。NULL = 未分类 |
| `balance_cents` | BIGINT NOT NULL DEFAULT 0 | 账户当前余额，单位分 |

三条与其它实体刻意不同的口径：

- **`balance_cents` 不带非负 CHECK**（对照 `savings_goals.current_cents` 的 `>= 0`）：信用卡欠款是常态，负余额必须能存。这是对上面「`MoneyCents` 存储层禁止负值」的**唯一例外**，范围仅限本列——客户端渲染负余额仍走既有 `Math.abs` + 手拼负号口径（`Money.cents` 的非负契约未放开）。
- **余额 ≠ 该账户交易净额**：它是**存量字段**，允许被用户手动校准，差额即手动校准量。AI 只读工具 `account_balances` 现算的是净额，两者不保证相等属正常状态。
- **上行恒传绝对值，不传增量**：账户 mutation 携带更新后的余额本身，故重放（离线队列重试 / pull-push 重放）不会重复累加。多设备各自离线记账后互相覆盖（后到者胜）是该取值的已知代价，登记为 R-PROD-43。

**同步保护口径（客户端 pull）**：`account_type` 在**服务端为空值**时保留本地值（存量行服务端仍是 NULL，而本地早已存着用户选的分类，照单全收会让它在一次 pull 后消失）；非空时服务端权威。`balance_cents` **不做**本地保护——`0` 是合法的服务端值，保护它就永远吃不下云端清零；本地乐观写入由既有「活跃 mutation 跳过合并」兜住。
