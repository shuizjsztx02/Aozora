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

## categories 表：图标两端化（T19-1 / REQ-73，ADR-014）

第十九轮把 `icon` **两端化**（此前只存在于客户端 Room：无列、insert/update 两个写入分支都不读、序列化也不出；pull 靠一层「恒保留本地」的保护才没被冲刷，bootstrap 路径连保护都没有——走一次快照重建，图标全丢）：

| 列 | 类型 | 说明 |
|---|---|---|
| `icon` | VARCHAR(64) NULL | 分类图标。**跨端只承载 ≤64 字符**（emoji / 短文本）；NULL = 「按名映射 emoji」（不是「无图标」） |

三条口径：

- **照片图标（`data:` URI，可达 48KB）刻意不上云**：客户端 `categoryToJson` 在超长时**省略 icon 键**，保持「仅本机可见」的现状。服务端 `_validate_name(icon, 64)` 只是兜底——**护栏不落地就会变成毒丸**：整批 push 422，不是单个分类失败（ADR-014 决策 2）。
- **push 侧 `null` 必须显式上行**：服务端 update 是「键在场才改」，省略键会让「清除自定义图标」（`IconPicker` 的真实操作）静默失效。
- **pull 侧三态合并**：本地是 `data:` → 保留本地；服务端非空 → 服务端权威（AI / 跨设备改图标只走这条）；服务端为 `null` → 保留本地（存量行全是 NULL，照单全收会抹掉本地 emoji）。第三条与上面的 `account_type` **同口径**。

**已知代价**：服务端 NULL 同时是「从未设置」与「他机清空」两种成因，而上面一律当「无信息」，故「清空图标」不能跨设备传播（须等对方走一次 bootstrap）。消除它需要显式哨兵值，本轮不做（ADR-014 后果 2）。

## auto_captures 表（客户端 Room v12，第二十一轮 REQ-83，ADR-016）

自动记账的采集层需要一张**只存在于客户端 Room**的表（服务端没有对应实体——采集发生在设备上，**上行的是解析结果与既有交易实体**，不是这张表本身）：承载「一条被捕获、尚未解析 / 尚未确认的**原始事件**」。

**为什么必须新开一张表而不是复用 `pending_mutations`**：后者装的是**已定型的写操作**（entityType / operation / payload / expectedVersion），语义是「要推给服务端的变更」；而捕获来的是一条**原始素材**，它有**独立生命周期**（捕获 → 解析 → 待确认 → 入账 / 忽略 / 失败）、需要**去重键唯一索引**、需要**原文留档**，且**两条通道写同一张表**（靠 `sourceChannel` 区分）。两者语义不符，**不得挪用**。

| 列 | 类型 | 说明 |
|---|---|---|
| `captureId` | TEXT PK | 捕获行唯一标识（客户端生成） |
| `dedupeKey` | TEXT **唯一索引** | L1 去重键：`hash(sourceChannel + sourcePackage + 事件 key/postTime + 归一化文本)`。唯一索引 + 冲突即**自动丢弃**——无障碍的 `TYPE_WINDOW_CONTENT_CHANGED` 与通知的重复回调都靠它收口 |
| `mergeKey` | TEXT NULL（**普通索引**） | L2 跨通道同笔键：`hash(方向 + 金额分 + 分钟级时间桶)`。**采集时留空**——方向与金额是**解析**才有的量，采集层不做解析（ADR-016 决策 4）；解析完成后由 `AutoBookRepository.mergeIfSameTransaction` 回填并落合并。**存成列而不是读取时重算**：重算要从 `rawText` 反解金额与方向，等于把解析器拖进读取路径，且无法建索引 |
| `sourceChannel` | TEXT | `accessibility` │ `notification`（两条通道的区分位） |
| `sourcePackage` / `sourceApp` | TEXT | 来源包名与应用名 |
| `rawText` | TEXT（限长） | 采集到的**原文**（无障碍按行拼装、通知取标题 + 正文）。非支付类内容**在此之前**已被过滤器丢弃，不会到达本列 |
| `postedAt` | INTEGER | 事件时间（通知 postTime / 页面事件时间，UTC ms） |
| `merchantKey` | TEXT NULL | 商户归一化键（用于商户记忆回查，第一期复用本表，不新开表） |
| `status` | TEXT | `captured` │ `parsing` │ `pending_confirm` │ `committed` │ `ignored` │ `failed` |
| `parsedJson` / `confidence` | TEXT / REAL | 本地规则或服务端解析的结果与置信度 |
| `transactionId` | TEXT NULL | 入账后指向生成的交易（`committed` 时非空） |
| `mergedInto` | TEXT NULL | 被 L2 合并时指向保留行（留痕，不静默删除） |
| `error` / `createdAt` / `updatedAt` | TEXT / INTEGER / INTEGER | 失败原因与时间戳 |

**状态机**：采集即 `captured` →（联网 / 本地规则）`parsing` → 高置信走快速通道 `committed`（**立即可见 + 可撤销**，PR-2）、低置信 / 缺字段 `pending_confirm`（进待确认箱）；用户忽略 → `ignored`；解析失败 → `failed`（**保留原文，不丢弃**，对齐 REQ-72「丢弃后游标照常推进＝静默永久丢失」的教训）。

**三级去重**（`DedupeKeys.kt`，**纯函数、确定性 hash、无 LLM 参与**；判据与理由见 `ADR-016` 决策 5 / 6）：

- **L1 同通道同事件** → `dedupeKey` 唯一索引，冲突自动丢弃。
- **L2 跨通道同笔** → `mergeKey = hash(方向 + 金额分 + 分钟级时间桶)` 命中**且**（两边都无商户名 **或** 商户名相同）→ 合并为一条，保留信息最全的一份（无障碍 > 通知），`mergedInto` 留痕，标 `multi_source`（= 指向本行的行数 > 0，**派生量，不另设列**）。**判定时机在解析之后**：采集层拿不到方向与金额（ADR-016 决策 4），故 `mergeKey` 由解析侧回填——这是「合并只解决不重复入账」的落点。
- **L3 同额但不可判定**（商户名不同 / 时间窗外）→ **不合并**，只标「疑似重复」提示（同为派生量）。

**写库契约不变**：`committed` 的落账仍一律走 `Repository.enqueueInsert`，与对应 `pending_mutations` **在同一 Room 事务内**（本表自身的写入是采集侧的本地记录，不入队 mutation）。迁移走 `MIGRATION_11_12`，**不开启 `fallbackToDestructiveMigration`**，导出 `12.json`。
