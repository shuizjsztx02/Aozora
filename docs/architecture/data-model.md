# Aozora · 数据模型约束

- 金额以正整数 `amountCents` 存储；收支方向由 `type` 表达。
- 登录用户的服务端数据是权威；Room 仅保存本地副本和待同步操作。
- 每个写操作携带 `mutationId`，服务端必须幂等处理。
- 可修改实体带 `version`，冲突必须显式返回，禁止静默覆盖。
- 交易同时保存 UTC `occurredAt` 和用户业务日期 `bizDate`。
- 统计口径：`MonthlyStats.balance = max(0, totalIncome − totalExpense)`。`MoneyCents` 禁止负值，赤字（支出 > 收入）不存入 balance，由展示层用 `totalIncome/totalExpense` 推导。客户端统计、服务端统计与导出必须使用同一口径，保证 APP-CONTEXT §5.2 对账不变量（客户端明细合计 = 客户端统计 = 服务端统计 = 导出文件合计）。
- 空预算语义：`Budget.categoryId` 与 `Category.bookId` 以显式 `null`（JSON 标准表达）或字段缺省表示“整体预算 / 系统分类”，客户端类型须接受 `string | null | undefined` 三种形态。

## Room schema v2（T-M3-02 同步引擎）

- **`sync_meta(key TEXT PK, value TEXT NOT NULL)`**：同步元数据键值表，现用键 `sync_cursor`（已合并到的服务端 cursor）。cursor 更新与实体合并必须在同一事务内。
- **`pending_mutations` 新增列**（`MIGRATION_1_2`，旧行默认 `NULL`）：
  - `expectedVersion INTEGER`：update/delete/archive 入队时捕获的变更前实体版本号，推送时作为服务端 `version` 期望值；insert 为 `NULL`。
  - `lastError TEXT`：失败原因（冲突、非重试错误等）。
- **`failed` 状态语义**：版本冲突与不可重试错误（422/404）为终态，保留本地待处理版本等待用户处理，自动同步不再重试该 mutation；瞬时错误保持 `pending` 由 WorkManager 退避重试。
- **同步合并豁免**：服务端数据合并仅经 Repository server-merge 路径（`upsertFromSync`，REPLACE 语义）且不产生新 mutation；业务写入 insert 保持 ABORT 语义不变。
- **wire 契约映射**：Room/Bridge 层 camelCase，服务端同步/业务 API 为 snake_case；`occurredAt`（UTC ms）↔ ISO-8601 UTC 字符串；客户端 `archive` ↔ wire `delete`。

完整实体和同步规则以 `docs/APP-CONTEXT.md` 第 5 节为准。
