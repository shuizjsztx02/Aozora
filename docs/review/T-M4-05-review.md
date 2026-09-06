# T-M4-05 通知、快捷方式与小组件 — 复审报告（第 2 轮）

- **审查日期：** 2026-09-06
- **审查范围：** T-M4-05a（提醒调度与通知）、T-M4-05b（桌面小组件）、T-M4-05c（快捷方式与深链路由）
- **审查基线：** HEAD `c101afb`（工作区干净，T-M4-05 代码随该提交入库——与 T-M4-02 混合提交，非按任务原子提交）
- **轮次说明：** 本轮为**第 2 轮复审**。第 1 轮审查文档未归档（`docs/review/` 无 T-M4-05 首轮记录），仅存代码注释线索：R-M4-21/22/25/26 的修复注记（R-M4-17 已被 T-M4-03 第 3 轮占用；R-M4-18~20/23/24 推测为首轮使用，无从核验）。**本轮新增问题编号自 R-M4-27 起**，沿用 M4 连续编号。
- **状态：** ⚠️ **复审不通过**（1 项 P1 + 2 项 P2 待修复；另 1 项 P2 属 T-M4-00 跨任务拆分）

---

## 一、总体评价

T-M4-05 三个子任务的主体工程质量良好：

- **05a** ReminderScheduler 采用 AlarmManager 单次闹钟 + 触发后链式重排，选型理由（精确每日提醒、Doze 下触发）在 KDoc 中记录；Android 12+ 无精确闹钟权限时降级 `setWindow` 不崩溃；BootReceiver 重启恢复；「先重排再发通知」保证权限被拒时调度链不中断——设计正确。
- **05b** WidgetDataHelper 直读 Room（Repository 注入可测），三状态（NO_DATA/PENDING_SYNC/READY）齐备，SyncWorker 成功后触发 `requestUpdate`（setPackage 包内广播，规避隐式广播限制）；`formatCents` 纯整数运算（R-M4-25 修复属实）。
- **05c** 深链 URI host 白名单映射 + `escapeJs` 注入防护 + 前端 `DeepLinkHandler` 校验 `startsWith('/')` + 路由 catch-all 兜底，链路完整；原生入口无旧 JSON 文件依赖（grep 证实 `org.json` 仅用于桥/同步/认证的线上序列化）。

但对照任务验收项，存在 1 项 P1 功能性断链（通知权限只查不请，Android 13+ 提醒功能事实上不可用）、2 项 P2 验收缺口（entry 深链未打开记账面板；小组件与首页聚合口径不一致），以及 1 项跨任务 P2（首页 `budget.list` 空 payload，真机上会连带击穿 05b「金额与首页一致」验收）。**判定：复审不通过**，详见第三节与第七节。

---

## 二、第 1 轮遗留修复核验（可查证部分）

| 编号 | 内容（据代码注释重建） | 核验结果 |
|---|---|---|
| R-M4-21 | Widget 数据/视图逻辑可测化：`WidgetDataHelper.loadData(repository)` Repository 注入 + `AozoraWidgetProvider.bindViews` 纯视图分离 | ✅ 修复属实——`WidgetDataHelperTest` 15 项（内存 Room 覆盖三状态/聚合口径/预算封顶）+ `AozoraWidgetProviderTest` 4 项（RemoteViews apply 后断言可见性与文本）本轮实测全绿 |
| R-M4-22 | ReminderSettingsPage 测试覆盖补充 | ✅ 修复属实——`ReminderSettingsPage.test.tsx` 9 项（加载态/开关回滚/时间滚轮 wrap/权限横幅），本轮实测全绿。注意其中「去开启」用例固化了 R-M4-27 的错误行为，修复时需同步改测试 |
| R-M4-25 | `formatCents` 弃用 Double、改纯整数运算 | ✅ 修复属实——`WidgetDataHelper.kt:106-110` 整数除法 + padStart；回归测试含 2^53+1 精度用例与负数符号位用例 |
| R-M4-26 | BootReceiver/ReminderReceiver 避免 Application 类型强转，改用 `context.applicationContext` 重建调度器 | ✅ 修复属实——两接收器均直建 `ReminderScheduler(applicationContext)`，状态经 SharedPreferences 共享，无强转 |
| R-M4-18~20 / 23 / 24 | （首轮文档缺失，代码无注记） | ❓ 无从核验——首轮审查文档未归档，本报告即为存档补位 |

---

## 三、本轮新增发现

### 🔴 P1 × 1

#### R-M4-27（P1 · 功能）POST_NOTIFICATIONS 运行时权限申请链路缺失——Android 13+ 提醒功能事实上不可用

- **文件：** `app/src/features/settings/ReminderSettingsPage.tsx:62-73`、`app/src/main/java/com/aozora/app/permission/PermissionManager.kt`、`app/src/platform/bridge/bridge-types.ts:311`、`app/src/main/java/com/aozora/app/bridge/AozoraBridge.kt:620-629`
- **问题：** 05a 验收明确要求「POST_NOTIFICATIONS 运行时权限（API 33+）」。当前实现只有**检查**、没有**申请**：
  1. bridge `reminder.checkPermission` / `reminder.getStatus` 仅调 `checkSelfPermission`，不弹系统授权框；
  2. `permissions.request` 的 `PermissionType` 联合类型与 Kotlin `keyToPermission()` 均只含 RECORD_AUDIO/CAMERA/READ_MEDIA_IMAGES，**无法申请 POST_NOTIFICATIONS**；
  3. 前端权限横幅「去开启」按钮实际只调 `checkReminderPermission()`（再查一遍）+ `alert` 文案提示，既未发起运行时申请、也未调 `permissions.openSettings` 跳系统设置；
  4. `ReminderSettingsPage.tsx:67` 注释「Android 系统会弹出权限请求（首次）」**与事实不符**——任何代码路径都不会触发系统弹框。
- **后果：** Android 13+ 设备上用户没有任何应用内途径授予通知权限 → `ReminderReceiver.onReceive` 每次触发都在 `hasNotificationPermission` 处静默 return → 提醒调度链空转，通知永远不展示。且开启提醒开关时也不检查/引导权限，用户以为设置成功。
- **修复建议：** ① `PermissionType` 与 `keyToPermission()` 增加 `POST_NOTIFICATIONS`（API 33+ 映射），复用现有 `permissions.request` 异步回调链；② 「去开启」改为：先 `requestPermission('POST_NOTIFICATIONS')`，返回 `permanently_denied` 时调 `openPermissionSettings()`；③ 开启提醒开关且无权限时主动触发一次申请；④ 同步修正 `ReminderSettingsPage.test.tsx:131-144` 固化错误行为的用例与错误注释。

### 🟡 P2 × 3（其一跨任务）

#### R-M4-28（P2 · 功能）`aozora://entry` 深链未打开 QuickEntrySheet——「快速记账」shortcut 与提醒通知点击等同普通启动

- **文件：** `app/src/main/java/com/aozora/app/MainActivity.kt:159`、`app/src/app/Router.tsx:30-46`
- **问题：** 原生侧 `resolveRoute()` 将 `entry` 映射为 `/home`，注释声称「前端收到后打开 QuickEntrySheet」；但前端 `DeepLinkHandler` 只做 `navigate(path)`，`/home` 与 entry 语义完全丢失，全代码库无任何自动打开 Sheet 的逻辑（grep `showSheet` 仅 HomePage 内手动按钮触发）。而静态 shortcut `quick_entry`（`shortcuts.xml:12`）与提醒通知点击 intent（`ReminderReceiver.kt:41`）都指向 `aozora://entry`。
- **后果：** 05c 验收「快捷入口打开正确页面」只达成一半——到达了首页，但「快速记账」的核心意图（直达记账面板）落空；提醒通知「记录今天的花销」点击后同样只是打开首页。
- **修复建议：** 原生侧保留白名单，将 entry 以可区分形式传递（如 `/home?entry=1` 或独立全局回调 `window.__aozoraQuickEntry__`）；HomePage mount/参数变化时 `setShowSheet(true)`。补前端测试断言深链 entry → Sheet 打开。

#### R-M4-29（P2 · 正确性）小组件与首页聚合口径不一致（账本范围 / 时区 / 预算筛选三处）

- **文件：** `app/src/main/java/com/aozora/app/widget/WidgetDataHelper.kt:63-80` vs `app/src/features/ledger/HomePage.tsx:35-57`
- **问题：** 05b 验收要求「金额与首页一致」，但三处口径分叉：
  1. **账本范围：** Widget 用 `listTransactionsByDateRange`（DAO 无 bookId 过滤，**全账本求和**）；首页用 `transaction.listByBook(books[0].id)`（**单账本**）。多账本时金额必然不一致。
  2. **时区：** Widget 用 `LocalDate.now()`（设备默认时区）；首页用 `todayBizDate('Asia/Shanghai')`（固定时区）。设备时区非 Asia/Shanghai 时「今日」边界不同，跨日时段金额不一致。
  3. **预算筛选：** Widget 要求 `period == month && budgetType == "monthly"` 且限定 `books[0]`；首页仅按 `period === month` 匹配（不查 budgetType）。
- **修复建议：** Widget 改用 `listByBookAndDateRange(books[0].id, ...)`（DAO 已有该方法）；「今日/本月」边界与前端 `todayBizDate` 统一口径（固定 Asia/Shanghai 或统一取设备时区，二选一全端对齐）；预算筛选条件与首页对齐（并顺带核对 R-M4-30 修复后首页的实际行为）。

#### R-M4-30（P2 · 跨任务，归属 T-M4-00）HomePage/ReportsPage `budget.list` 传空 payload——真机上首页整体加载失败，连带击穿 05b 验收

- **文件：** `app/src/features/ledger/HomePage.tsx:45`、`app/src/features/reports/ReportsPage.tsx:44` vs `app/src/main/java/com/aozora/app/bridge/AozoraBridge.kt:314-315`
- **问题：** 两页面调用 `bridge.call('budget.list', {})`，而 Kotlin `handleBudgetList` 用 `p.getString("bookId")` **必填**取值 → JSONException → `internal_error` → HomePage 的 `Promise.all` 整体 reject → 今日支出/近期流水/预算全部不渲染。前端测试与视觉基线全部使用 mock bridge（对任意 payload 返回 fixture），**掩盖了该契约不匹配**；T-M4-06 复审的「Mock Bridge 协议一致性核查」只对了签名与响应格式，未对每命令 payload 契约。
- **影响范围：** 该缺陷属 T-M4-00 遗留（非 T-M4-05 引入），但真机上首页无数据 → 05b「金额与首页一致」验收无从成立，故在本报告登记并**建议单开 T-M4-00 缺陷单**，不阻塞 05a/05c 修复。
- **修复建议：** 前端补传 `{ bookId }`（两页面都已先取 books[0].id，一行改动 ×2）；或 Kotlin 侧 `optString("bookId")` 缺省返回全部账本预算。修复后补一条「payload 契约」级别的桥测试（真实 Kotlin dispatch + 前端调用参数对账）。

### 🟢 P3 × 7（建议改进，不阻塞验收）

#### R-M4-31（P3 · 口径）Widget PENDING_SYNC 判定仅统计 `status='pending'`

- **文件：** `app/src/main/java/com/aozora/app/db/dao/PendingMutationDao.kt:36-37`、`WidgetDataHelper.kt:88-89`
- `countPendingMutations` 不含 `syncing`（崩溃恢复残留）与 `failed`（被服务端拒绝）状态；此类场景 Widget 显示 READY，而首页 SyncBadge 依据 failedMutations 会提示异常。建议口径对齐前端 sync-store（pending+syncing+failed 视为「待处理」或分色提示）。

#### R-M4-32（P3 · 性能）`onUpdate` 主线程 `runBlocking` 查询 Room

- **文件：** `WidgetDataHelper.kt:44-47`、`AozoraWidgetProvider.kt:63-82`
- 广播 `onReceive/onUpdate` 在主线程执行，`loadData(context)` 以 `runBlocking` 同步查 Room（今日 + 本月两次范围查询 + budgets + pending count）。数据量大或 DB 被写事务占用时有卡顿/ANR 风险。注释已自认此权衡；建议后续改 `goAsync()` + 协程后台查询后 `updateAppWidget`。

#### R-M4-33（P3 · 健壮性）深链重放：Activity 重建后旧 intent 再次导航；EXTRA_ROUTE 未白名单校验

- **文件：** `MainActivity.kt:72-77,138-170`
- `routeDeepLink()` 挂在每次 `onPageFinished` 上且读取当前 intent；旋转/后台回收重建后旧 `aozora://` data 仍在 → 用户已被拉回深链目标页（典型 deep link replay）。另外 `EXTRA_ROUTE` 分支接受任意字符串直接透传前端（仅 escapeJs 防注入 + 前端 `startsWith('/')` 校验，任意应用可借 exported MainActivity 触发应用内任意路由跳转——影响低，但与 URI 分支的白名单不对称）。建议：路由完成后 `intent.data = null` / removeExtra，EXTRA_ROUTE 同样过白名单。

#### R-M4-34（P3 · 体验）`widget_info.xml` 缺 `android:label`；`previewLayout` 仅 API 31+ 生效

- **文件：** `app/src/main/res/xml/widget_info.xml`
- `widget_label`（"Aozora 记账"）字符串已定义但未引用，选择器中显示应用名；`previewLayout` 为 API 31+ 属性，minSdk 26–30 设备无 `previewImage` 兜底，选择器显示默认占位图。

#### R-M4-35（P3 · 边界）ReminderScheduler 不响应 `TIME_SET` / `TIMEZONE_CHANGED`

- **文件：** `ReminderScheduler.kt:101-127`
- 闹钟按绝对时间戳登记，用户改时钟/时区后已登记闹钟不重算（触发一次后才按新时间链式重排），期间提醒时间漂移。影响小、发生率低，挂账。

#### R-M4-36（P3 · 契约）`reminder.set` 缺失字段返回 `internal_error` 而非 `invalid_input`

- **文件：** `AozoraBridge.kt:434-443`
- `p.getBoolean/getInt` 缺字段抛 JSONException → 落入兜底 `internal_error`；与同文件 AI handlers 的「C-M4 fix：缺失字段 → invalid_input」约定不一致（hour/minute 越界的 IllegalArgumentException 已正确映射 invalid_input）。建议改 `opt*` + 显式校验。

#### R-M4-37（P3 · 文档）RemoteViews / Glance 选型理由未记录

- **文件：** `AozoraWidgetProvider.kt` KDoc、`production-execution-plan.md` T-M4-05 完成记录
- 05b 交接产物要求「RemoteViews / Glance 选型记录」；完成记录仅写「AozoraWidgetProvider（RemoteViews）」，无决策理由（对比：05a 的 AlarmManager 选型理由在 KDoc 中完整）。建议补一句：Glance 需引入 Compose 依赖链、当前 widget 为静态三状态展示，RemoteViews 零额外依赖且 Robolectric 可测。

---

## 四、验收确认

### T-M4-05a 提醒调度与通知

| 验收项 | 结果 | 证据 |
|---|---|---|
| ReminderScheduler + 选型记录（WorkManager/AlarmManager） | ✅ | `ReminderScheduler.kt` KDoc：精确每日提醒、Doze 下触发、Android 12+ 降级 setWindow、链式重排理由完整 |
| POST_NOTIFICATIONS 运行时权限（API 33+） | ❌ | **R-M4-27**：只有检查、无申请链路；「去开启」按钮不触发系统弹框也不跳设置 |
| bridge reminder.* 命令 | ✅ | getStatus/set/checkPermission 3 命令；Kotlin/TS 两侧常量对齐（BridgeProtocolTest 命令清单用例通过）；未注入时返回 unavailable |
| 我的页提醒设置入口 | ✅ | `MePage.tsx:102` → `/settings/reminder`；Router 已注册（含 ProtectedRoute） |
| BOOT_COMPLETED 恢复 | ✅ | `BootReceiver` + manifest `RECEIVE_BOOT_COMPLETED`；`rescheduleIfEnabled` 测试通过 |
| 重启设备后提醒仍有效 | ✅/⏳ | 逻辑层 Robolectric 验证通过；**真机复验待做**（本环境无设备） |
| 通知权限拒绝不崩溃 | ✅ | `ReminderReceiver.onReceive:34` 先查权限即 return；且先重排后发通知，调度链不断 |
| 设置修改即时生效 | ✅ | `setReminder` 同步写 prefs + 立即 schedule/cancel |

### T-M4-05b 桌面小组件

| 验收项 | 结果 | 证据 |
|---|---|---|
| Widget Provider + 选型记录（RemoteViews/Glance） | ⚠️ | RemoteViews 实现正确；选型**理由**未记录（R-M4-37） |
| 读 Room 聚合（今日支出/本月进度） | ✅ | `WidgetDataHelper` → Repository → Room；口径问题见 R-M4-29 |
| 监听 SyncEventBus 同步后刷新 | ✅ | `SyncWorker.doWork` OK 分支 → `requestUpdate`（setPackage 广播）；`AozoraWidgetProviderTest` 断言广播包名限定 |
| 登录/无数据/待同步三状态 | ✅ | NO_DATA/PENDING_SYNC/READY 三态实现+测试；「登录」态并入 NO_DATA（无账本），完成记录已如实声明并获父任务接受 |
| 点击跳转接线 | ✅ | widget_root 点击 → MainActivity（精确页面路由为完成记录已声明的挂账项） |
| **金额与首页一致** | ❌ | **R-M4-29**（三处口径分叉）+ **R-M4-30**（真机首页加载失败，跨任务） |
| 同步后刷新 / 三状态展示正确 | ✅ | 测试覆盖（15+4 项全绿） |

### T-M4-05c 快捷方式与深链路由

| 验收项 | 结果 | 证据 |
|---|---|---|
| 静态 shortcuts（快速记账） | ⚠️ | `shortcuts.xml` quick_entry 已注册；但点击后未打开记账面板（**R-M4-28**）。AI 助手 shortcut 与动态 shortcuts 为完成记录已声明挂账（依赖 T-M4-03c） |
| WebView 深链路由接线（入口参数 → 前端路由） | ✅ | URI host 白名单 → `__aozoraNavigate__` → HashRouter；onNewIntent/onPageFinished 双路径；注入防护（escapeJs）到位 |
| 全部原生入口统一经 Repository 读 Room，无旧 JSON 依赖 | ✅ | widget/reminder/shortcut 入口均走 Repository 或纯配置（SharedPreferences 仅存提醒设置，非业务数据）；grep 证实无文件型 JSON 存储 |
| **每个 shortcut 与小组件点击真机到达正确页面** | ⚠️/⏳ | 逻辑上到达首页 ✅，但 entry 语义丢失（R-M4-28）；**真机复验待做** |

### APP-CONTEXT §9 父任务验收（T-M4-05）

| 验收项 | 结果 |
|---|---|
| 重启后提醒有效 | ✅（逻辑层；真机待复验） |
| 小组件与首页金额一致 | ❌ R-M4-29 / R-M4-30 |
| 快捷入口打开正确页面 | ⚠️ R-M4-28（到达首页但未达记账面板） |
| 无旧 JSON 依赖 | ✅ |

---

## 五、独立验证结果（本轮实测）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 前端全量测试 | `npx vitest run` | ✅ **398/398 通过**（22 files） |
| 前端类型检查 | `npx tsc --noEmit` | ✅ 零错误 |
| Kotlin 单测（本任务范围） | `./gradlew testDebugUnitTest --tests "com.aozora.app.reminder.*" --tests "com.aozora.app.widget.*" --tests "com.aozora.app.bridge.*"` | ✅ BUILD SUCCESSFUL——ReminderSchedulerTest **9**、WidgetDataHelperTest **15**、AozoraWidgetProviderTest **4**、BridgeProtocolTest **6**，共 34 项 0 失败 |
| HEAD 自洽性 | `git status`（干净）+ 上述测试跑于工作区=HEAD | ✅ 本任务范围无未提交差异（对比 T-M4-03 的 R-M4-17 问题不存在）；但 T-M4-05 代码混入 `c101afb feat(T-M4-02)` 提交，非按任务原子提交，记流程瑕疵 |
| 真机项 | 重启后提醒、shortcut/widget 点击到达页面 | ⏳ 本环境无设备，待修复后一并真机复验 |

---

## 六、缺陷汇总

| 编号 | 优先级 | 子任务 | 描述 | 状态 |
|---|---|---|---|---|
| R-M4-27 | P1 | 05a | POST_NOTIFICATIONS 运行时权限申请链路缺失，Android 13+ 提醒不可用；「去开启」按钮与注释误导 | 🔴 待修复 |
| R-M4-28 | P2 | 05c | `aozora://entry` 未打开 QuickEntrySheet，快速记账 shortcut/提醒通知点击等同普通启动 | 🔴 待修复 |
| R-M4-29 | P2 | 05b | Widget 与首页聚合口径不一致（跨账本求和 / 时区 / 预算筛选） | 🔴 待修复 |
| R-M4-30 | P2 | 跨任务→T-M4-00 | HomePage/ReportsPage `budget.list` 空 payload → 真机首页整体加载失败 | 🔴 建议单开 T-M4-00 缺陷单 |
| R-M4-31 | P3 | 05b | PENDING_SYNC 仅统计 pending 状态，与首页 SyncBadge 口径不一致 | 挂账 |
| R-M4-32 | P3 | 05b | onUpdate 主线程 runBlocking 查 Room，ANR 风险 | 挂账 |
| R-M4-33 | P3 | 05c | 深链重放（Activity 重建）；EXTRA_ROUTE 未白名单 | 挂账 |
| R-M4-34 | P3 | 05b | widget_info 缺 label；API 26–30 无 previewImage 兜底 | 挂账 |
| R-M4-35 | P3 | 05a | 不响应 TIME_SET/TIMEZONE_CHANGED，改时钟后提醒漂移 | 挂账 |
| R-M4-36 | P3 | 05a | reminder.set 缺字段返回 internal_error，契约不一致 | 挂账 |
| R-M4-37 | P3 | 05b | RemoteViews/Glance 选型理由未记录 | 挂账 |

---

## 七、结论

**实现层：❌ 不通过。** 05a/05b/05c 的调度链、三状态小组件、深链路由主体实现正确且测试覆盖扎实（首轮 R-M4-21/22/25/26 修复全部核验属实）；但 R-M4-27（P1）使 Android 13+ 的提醒通知功能事实上不可用且 UI 存在误导，R-M4-28/29（P2）分别击穿「快捷入口打开正确页面」与「金额与首页一致」两条父任务验收。R-M4-30 属 T-M4-00 遗留，但在真机验收时会与 R-M4-29 叠加。

**交付层：✅ 通过（带流程瑕疵）。** HEAD 自洽、工作区干净、前端 398 + 本任务 Kotlin 34 项测试全绿；瑕疵为 T-M4-05 代码混入 T-M4-02 提交（非原子），以及首轮审查文档未归档（本报告补位）。

**建议承接顺序：**
1. `修复 T-M4-05-review 中 R-M4-27`（P1：PermissionType/keyToPermission 增加 POST_NOTIFICATIONS + 「去开启」走真实申请/跳设置 + 开关时主动引导 + 修正测试与注释）
2. `修复 T-M4-05-review 中 R-M4-28`（entry 深链 → QuickEntrySheet 自动打开）
3. `修复 T-M4-05-review 中 R-M4-29`（Widget 改 listByBookAndDateRange + 时区/预算口径对齐首页）
4. `修复 T-M4-00 缺陷 R-M4-30`（budget.list 补 bookId，单独缺陷单）
5. 上述修复后真机复验：重启提醒有效、shortcut/通知/widget 点击到达正确页面、widget 与首页金额一致
6. R-M4-31~37 可随后续任务顺带处理，不单独开工

**父任务状态建议：** `production-execution-plan.md` T-M4-05 行由「✅ 已完成」改回「⚠️ 复审不通过」，待 R-M4-27/28/29 修复并真机复验后再恢复。
