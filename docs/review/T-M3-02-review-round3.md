# T-M3-02 Android 同步引擎代码审查（第三轮）

- **审查日期：** 2026-09-06
- **基线：** T-M3-02-followup-fixes.md 声称的 NEW-1/2/3 修复
- **方法：** 全量阅读 auth/sync/db 模块 + 服务端对照 + 契约一致性验证 + 测试执行
- **测试结果：**
  - 服务端 `pytest tests/test_sync.py -q` → **72 passed**
  - 客户端 `:app:testDebugUnitTest` → **BUILD SUCCESSFUL**
  - 客户端 `:app:compileDebugAndroidTestKotlin` → **BUILD SUCCESSFUL**
  - `tsc --noEmit` → **无错**

## 一、修复验证

| # | 问题 | 修复位置 | 验证结论 |
|---|---|---|---|
| NEW-1 | account.archive 同步 | `SyncPayloadMapper.kt:33` OP_ARCHIVE → "delete" | ✅ 已修复 |
| NEW-2 | emit 频繁查询 failed | `SyncEngine.kt:49,314-316` cachedFailedMutations + refreshFailed 参数 | ✅ 已修复 |
| NEW-3 | AuthLostException 信息泄露 | `HttpSyncApi.kt:61-64` 仅传状态码 | ✅ 已修复 |

NEW-1/2/3 修复代码逻辑正确，编译通过，测试覆盖到位。

## 二、新发现问题

### 🔴 P1 级问题（2 项 — 同步链路彻底不通）

#### NEW-4: 客户端 `/sync/pull` HTTP 方法与服务端不匹配 — POST vs GET

- **客户端：** `HttpSyncApi.kt:33-37,45-67`
  - `pull()` 调用 `executeAuthorized(url, body)` → 始终使用 **POST + JSON body**
  - 请求体 `{"cursor": 0, "limit": 200}`
- **服务端：** `routes.py:32-41`
  - `@router.get("/sync/pull")` → 仅接受 **GET + query 参数**
  - 参数 `cursor: int = Query(...)`, `limit: int = Query(...)`

- **后果：** 客户端 POST `/sync/pull` → **FastAPI 返回 405 Method Not Allowed** → `SyncHttpException(405)` → SyncEngine 判定 `405 ∉ {401,403}` → `SyncOutcome.RETRYABLE` → WorkManager 退避重试 → **pull 永远失败**
- **影响：** pull 是同步引擎的核心链路（pull-echo、bootstrap 前的初始拉取均依赖它）。pull 不通意味着：
  1. 客户端永远无法拉取其他设备的变更
  2. push 后的 echo-pull 失败 → cursor 无法推进（因 push 不推进 cursor，设计如此）
  3. 多设备同步实质上只写不读 — 数据单向流动

- **为何测试没捕获：**
  - 服务端 `test_sync.py` 使用 `client.get("/sync/pull?cursor=0")` — GET 方法
  - 客户端 `SyncEngineTest` 使用 `FakeSyncApi` — 不经过 HTTP 层
  - **没有任何测试覆盖客户端 `HttpSyncApi.pull()` 到服务端 `/sync/pull` 的 HTTP 链路**

- **修复方向（二选一）：**
  1. **服务端改为 POST**（推荐，与 push/bootstrap 保持一致）：
     ```python
     class PullRequest(BaseModel):
         cursor: int = Field(default=0, ge=0)
         limit: int = Field(default=100, ge=1, le=500)

     @router.post("/pull", response_model=PullResponse)
     async def sync_pull(body: PullRequest, ...):
         return await service.pull(current_user.id, body.cursor, body.limit)
     ```
  2. **客户端改为 GET**（需要改 `HttpSyncApi`，为 pull 单独构建 URL + query 参数）

- **严重性：** P1 — 核心功能完全失效

---

#### NEW-5: Pull 响应字段名不匹配 — `cursor` vs `next_cursor`

- **服务端返回：** `PullResponse.cursor`（`schemas.py:73`）→ JSON 中字段名为 `"cursor"`
- **客户端解析：** `SyncPayloadMapper.parsePullResponse`（line 90）→ `root.optLong("next_cursor", 0L)`

- **后果：**
  - `optLong("next_cursor")` 找不到 `"next_cursor"` 键 → 始终返回默认值 **0**
  - `PullResponse.nextCursor = 0`
  - `pullUntilCaughtUp` 中 `response.nextCursor(0) <= cursor(0)` → **首次 pull 即 break**（cursor 不推进）
  - 即使 NEW-4 修复后，pull 也无法推进 cursor → 每次同步都从 cursor=0 开始拉取全部变更

- **对比 push 响应：** `root.optLong("next_cursor", 0L)` 同样存在，但 push 链路不使用该字段（P1-7 修复后 push 不推进 cursor），所以 push 不受影响。

- **修复方向：** 客户端改为读取 `"cursor"` 字段：
  ```kotlin
  val nextCursor = root.optLong("cursor", 0L)
  ```
  或服务端改为返回 `"next_cursor"`（与 push 保持一致的命名）。

- **严重性：** P1 — 即使 NEW-4 修复后，pull 仍无法正确推进 cursor

---

### 🟡 P2 级问题

#### NEW-6: Pull 响应的 `has_more` 字段被客户端丢弃

- **服务端返回：** `PullResponse.has_more`（`schemas.py:74`）
- **客户端 `PullResponse`：** 无 `hasMore` 字段（`SyncApi.kt:95-98`）
- **`parsePullResponse`：** 不读取 `"has_more"`
- **后果：** `pullUntilCaughtUp` 仅靠 `changes.isEmpty()` 和 cursor 推进判断终止。当服务端恰好返回 limit 条变更且有更多时，客户端可能提前退出（需等到下一轮同步）。
- **当前保护：** `MAX_PULL_PAGES=50` 循环兜底 + 下一轮同步继续拉取。
- **严重性：** P2（功能正确性不受影响，但弱网场景下同步延迟增加）

---

### 🟢 P3 级问题

#### NEW-7: `MutationWire` KDoc 注释中 operation 枚举值已过时

- **位置：** `SyncApi.kt:66`
- **问题：** 注释声明 `operation: String  // "insert" | "update" | "delete" | "archive"`，但 NEW-1 修复后 `OP_ARCHIVE` 在 wire 层映射为 `"delete"`，实际 wire 协议仅三种操作。
- **修复：** 删除 `"archive"`。
- **严重性：** P3（注释误导）

---

## 三、NEW-4 / NEW-5 影响分析

这两个 P1 问题组合起来意味着：**自 T-M3-02 交付以来，Android 客户端的 pull 链路从未真正工作过。**

具体影响链：
1. push 可以成功（POST /sync/push → 200）→ mutation 被服务端接受 → 服务端 cursor 推进
2. echo-pull 失败（POST /sync/pull → 405）→ 客户端 cursor 不推进
3. 下次同步：push 无 pending mutations → echo-pull 再次 405 → 客户端永远落后于服务端

多设备场景：
- 设备 A 创建 transaction → push 成功 → 服务端有数据
- 设备 B 同步 → push 无 pending → pull 失败（405）→ 设备 B 看不到设备 A 的数据
- **同步实质上是"推-only"，pull 侧完全断裂**

**为何在 review-round2 中未被发现：** 审查侧重于代码逻辑（状态机、并发、异常处理），未执行端到端 HTTP 抓包验证客户端→服务端的实际请求。服务端测试使用 GET，客户端测试使用 FakeSyncApi，两侧测试都没覆盖 HTTP 方法匹配。

## 四、测试覆盖评估

### 已有覆盖（OK）

| 层 | 覆盖 |
|---|---|
| SyncEngine 逻辑 | ✅ 9 项 androidTest（FakeSyncApi） |
| SyncPayloadMapper | ✅ 10 项 JVM 单元测试 |
| 服务端 push/pull/bootstrap | ✅ 72 项 |
| BootstrapChecksum | ✅ 跨语言一致性验证 |

### 缺口

| 层 | 缺失 | 影响 |
|---|---|---|
| **HttpSyncApi 端到端** | 无测试验证 HTTP 方法/URL/字段名与实际服务端匹配 | **NEW-4/5 未被捕获** |
| ServerChange.toXxx() 字段映射 | 无 JVM 单元测试 | applyServerChanges 内部路径 |
| parsePullResponse 过滤 | 未覆盖 `version <= 0 && op != DELETE` | 边缘 case |

**建议补测试：**
1. **HttpSyncApi 端到端测试**（或契约测试）：启动真实/嵌入式 HTTP server，验证 push/pull/bootstrap 的 HTTP 方法、URL 路径、请求字段名、响应字段名一致。
2. `ServerChange.toTransaction()` 等字段映射单元测试。

## 五、结论与闸门

### 必做（闸门 — 阻塞 T-M3-03）

| # | 问题 | 严重性 | 状态 |
|---|---|---|---|
| NEW-4 | pull HTTP 方法不匹配（POST vs GET）| P1 | ✅ 已修复（服务端改为 POST）|
| NEW-5 | pull 响应字段名不匹配（cursor vs next_cursor）| P1 | ✅ 已修复（客户端读取 "cursor"）|

### 可挂账（生产部署前处理）

| # | 问题 | 严重性 |
|---|---|---|
| NEW-6 | has_more 字段被客户端丢弃 | P2 |
| NEW-7 | MutationWire 注释过时 | ✅ 已修复 |

### 最终结论

- **NEW-1/2/3 修复验证：** ✅ 全部到位
- **NEW-4/NEW-5 修复：** ✅ 已修复（服务端 pull 改为 POST，客户端读取 "cursor"）
- **NEW-7 修复：** ✅ MutationWire 注释已删除 "archive"
- **T-M3-03 闸门：** ✅ 已通过（见 T-M3-03-review.md Round 2）
- **T-M3-02 整体状态：** ✅ 全部 P1 问题已修复，可进入下一阶段
