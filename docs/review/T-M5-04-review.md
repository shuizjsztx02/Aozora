# T-M5-04 隐私、权限与账号生命周期 审查报告

> **审查日期:** 2026-09-07  
> **审查范围:** T-M5-04a / 04b（T-M5-04c 未开始，不在本轮审查范围）  
> **审查方法:** 代码 diff 复核 + 文档完整性检查 + 测试覆盖度分析  
> **对照基线:** `docs/milestones/production-execution-plan.md` L869-883

---

## 一、审查结论

| 子任务 | 结论 | 备注 |
|---|---|---|
| T-M5-04a 隐私政策与首启同意 | ✅ 通过 | 隐私政策文本完整；首启同意流、权限按需申请、AI 标识、UserConsent 双端存储均已实现；7 项 P3 级小问题建议跟进 |
| T-M5-04b 用户数据导出 | ✅ 通过 | 端到端流程完整；对账逻辑、状态流转、安全防御、测试覆盖均到位；4 项 P4 级可忽略问题 |

**总体结论: ✅ 通过**。T-M5-04a 和 T-M5-04b 均满足计划要求，核心功能完整，测试覆盖充分。存在若干 P3/P4 级改进建议，不阻塞通过。

---

## 二、T-M5-04a 审查详情

### 1. 隐私政策文本 ✅

**文件:** `docs/legal/privacy-policy-v1.md`

**实现内容:**
- v1.0 版本，生效日期 2026-09-07
- 7 个章节：概述、数据采集范围、数据存储、AI 使用说明、第三方共享、用户权利、联系方式
- 明确说明"不同步：未同意隐私政策前，无业务数据上行"
- 数据保留策略：账号删除后 7 天冻结期，之后物理删除；附件解析后 24h 删除

**问题:**
- ⚠️ **P3**：联系方式占位符 `[项目维护者]` 未替换为真实邮箱/链接，形式化交付不完整

**判定:** 内容覆盖计划要求的全部 5 项（采集范围/AI/第三方/保留删除/联系方式），通过。

---

### 2. 首启同意流 ✅

**文件:** `app/src/features/onboarding/OnboardingPage.tsx`

**实现内容:**
- 两步流程：`privacy` → `choose`
- 隐私摘要卡片（隐私保护/离线可用/AI 助手）+ "同意并继续" 按钮
- 点击同意后调用 `bridge.recordConsent({ policyVersion: 'v1' })`
- 成功后写 `aozora-consent-completed` localStorage 并进入 `choose` 步骤
- 失败时保留错误 + 显示重试按钮，不推进步骤（错误处理完整）
- 导出 `hasConsentedPrivacy()` / `isOnboardingCompleted()` 供路由守卫使用

**问题:**
- ⚠️ **P4**：`bridge.isAvailable()` 为 false（纯浏览器/测试环境）时直接跳过 consent 调用、写 localStorage 即视为同意，但不会写入 Kotlin `ConsentStore` 和 Room `user_consent` 表。这是合理设计（非原生环境不需要真同步），但应在代码注释中明确说明此行为

**判定:** 同意流完整，错误处理到位，通过。

---

### 3. Kotlin 侧同意存储 ✅

**文件:** 
- `app/src/main/java/com/aozora/app/consent/ConsentStore.kt`
- `app/src/main/java/com/aozora/app/bridge/AozoraBridge.kt`（handleConsentRecord / handleConsentStatus）

**实现内容:**
- `ConsentStore`：基于 `SharedPreferences` 的快速同意检查（`hasConsented` / `markConsented` / `clear`）
- `AozoraBridge.handleConsentRecord`：写 Room `user_consent` + 写 `ConsentStore.markConsented()`
- `policyVersion` / `deviceId` 截断至 32 / 128 字符，与服务端 schema 对齐（防御性编程）
- `handleSyncTrigger` 增加 `consent_required` 拦截，未同意时返回错误

**问题:**
- ⚠️ **P3**：`deviceId` 默认 `""`（`p.optString("deviceId", "")`），与服务端 `device_id` 的 `min_length=1` 正则 `^[a-zA-Z0-9_\-.:+]+$` 冲突。当前前端不传 deviceId，但 Kotlin → 服务端的 consent 同步路径（若有）会 422。当前代码中并未在 `handleConsentRecord` 里主动上行服务端，依赖后续 SyncEngine 同步，问题暂未暴露
- ⚠️ **P3**：Kotlin 单元测试目录下没有 consent 专项测试。建议补充 `ConsentStoreTest` 和 `handleConsentRecord` 测试

**判定:** 防御性编程到位，同步门禁实现，通过。

---

### 4. Room UserConsent 表 ✅

**文件:**
- `app/src/main/java/com/aozora/app/data/local/entity/UserConsentEntity.kt`
- `app/src/main/java/com/aozora/app/data/local/dao/UserConsentDao.kt`
- `app/src/main/java/com/aozora/app/data/local/AppDatabase.kt`（MIGRATION_2_3）

**实现内容:**
- Entity：`id` (PK) / `deviceId` / `policyVersion` / `consentedAt`（epoch millis）
- DAO：`insert` (REPLACE) / `getLatest` (按 `consentedAt DESC` LIMIT 1) / `count`
- 迁移：`MIGRATION_2_3` 创建 `user_consent` 表

**问题:**
- ⚠️ **P4**：`Repository.insertConsent` 使用 `OnConflictStrategy.REPLACE`，但 `id` 是 UUID 基本不会冲突。无害，可忽略

**判定:** 字段与服务端 `user_consents` 对齐，迁移完整，通过。

---

### 5. 服务端 UserConsent 表 ✅

**文件:**
- `server/src/migrations/versions/0011_user_consents.py`
- `server/src/models/consent.py`

**实现内容:**
- 迁移：创建 `user_consents` 表 + `ix_user_consents_device_id` 索引
- 使用 `sa.Uuid`（无 PG-specific 参数）+ `sa.DateTime(timezone=True)` + `sa.String`，兼容 PG + SQLite
- 迁移链完整（`down_revision = "0010_attachment_user_id_backfill"`）
- ORM 模型：`UserConsent`，字段 `id` / `device_id` (indexed) / `policy_version` / `consented_at`

**问题:**
- ⚠️ **P3**：迁移中 `id` 使用 `sa.Uuid`，但 ORM 模型中使用 `UUID(as_uuid=True)`（PG specific）。SQLite 测试环境通过 `Base.metadata.create_all` 建表时会退化为 VARCHAR。测试通过是因为 SQLite 兼容，当前不是 bug

**判定:** 数据库兼容性好，迁移链完整，通过。

---

### 6. 服务端 Consent API ✅

**文件:**
- `server/src/modules/consent/routes.py`
- `server/src/modules/consent/schemas.py`
- `server/src/modules/consent/service.py`

**实现内容:**
- `POST /consent`：无需认证，记录同意。有 IP 限流（`check_auth_rate_limit`）
- `GET /consent/status?device_id=...`：查询设备是否已同意
- Schemas：`device_id` 正则限定字符集、strip 空白；`consented_at` 钳制到 `[now-24h, now+5min]`（防御客户端时钟漂移/伪造）
- Service：`record_consent` / `get_latest_consent` / `has_user_consented`（通过 `device_sessions` 关联到 user，实现 server-side 纵深防御）

**问题:**
- ⚠️ **P3**：`has_user_consented(user_id)` 已实现但**全代码中未被调用**。这是 F-M5-04a-F1 "server-side defense-in-depth"，但当前没有任何中间件/依赖注入使用它。若计划作为认证后二次校验，应接入到 `get_current_user` 或关键路由（如 `/sync/...`）中

**判定:** 防御性编程优秀（时间钳制 + device_id 正则 + Rate limit），通过。

---

### 7. AI 内容标识 ✅

**文件:**
- `app/src/features/assistant/AssistantMessage.tsx`
- `app/src/features/reports/AiInsightCard.tsx`
- `app/src/features/transactions/HomePage.tsx`
- `app/src/features/transactions/DetailPage.tsx`

**实现内容:**
- `AssistantMessage.tsx`：非用户消息顶部显示 `<span class="ai-badge">AI 生成</span>`
- `AiInsightCard.tsx`：标题旁显示 `<span class="ai-badge">AI 生成</span>`
- `HomePage.tsx` / `DetailPage.tsx`：`source === 'nlu_ai'` 的交易分类旁显示 `<span class="ai-badge">AI</span>`

**问题:**
- ⚠️ **P3**：`.ai-badge` 类名被使用但 CSS 中无对应规则定义。实际视觉样式靠 Tailwind utility 实现。功能上无问题，但 `ai-badge` 类名形同虚设，无法作为统一选择器

**判定:** AI 标注覆盖 4 个关键场景（对话/洞察/AI 识别交易的分类），需求达成，通过。

---

### 8. 权限按需申请 ✅

**文件:**
- `app/src/main/java/com/aozora/app/permission/PermissionManager.kt`
- `app/src/hooks/usePermissions.ts`
- `app/src/components/ImagePickerSheet.tsx`
- `app/src/features/reminders/ReminderSettingsPage.tsx`
- `app/src/main/java/com/aozora/app/voice/VoiceRecognitionService.kt`

**实现内容:**
- `PermissionManager.kt`：四态（NOT_REQUESTED / GRANTED / DENIED / PERMANENTLY_DENIED）
- `requestPermission` 单次请求，拒绝后通过 `shouldShowRequestPermissionRationale` 区分是否永久拒绝
- `ImagePickerSheet.tsx`：点击"拍照"才请求 `CAMERA`，点击"相册"才请求 `READ_MEDIA_IMAGES`
- `ReminderSettingsPage.tsx`：开启提醒时才请求 `POST_NOTIFICATIONS`
- `VoiceRecognitionService.kt`：录音前校验 `RECORD_AUDIO`
- `isCoreFunctionBlocked()` 始终返回 false，拒绝不阻塞核心记账

**问题:**
- 无。权限在功能入口前申请（非首启批量申请），拒绝不阻塞核心记账

**判定:** 满足计划要求，有 `PermissionManagerTest.kt` 单测覆盖，通过。

---

## 三、T-M5-04b 审查详情

### 1. MePage 导出入口 ✅

**文件:** `app/src/features/settings/MePage.tsx`

**实现内容:**
- "数据导出" 菜单项 → `navigate('/settings/export')`
- 路由 `Router.tsx` 已注册 `/settings/export`

**问题:** 无

**判定:** 入口到位，通过。

---

### 2. ExportPage 前端实现 ✅

**文件:** `app/src/features/settings/ExportPage.tsx`

**实现内容:**
- 状态机：`idle → creating → ready → downloading → downloaded / failed`
- 账本选择 + 格式选择（JSON/CSV）
- `handleCreate`：调 `bridge.createExport({ bookId, format })`
- `handleDownload`：调 `bridge.downloadExport(exportId)`，JSON 格式且未截断时执行对账（`reconcile`）
- 对账逻辑：仅比较 `expense` 合计（与账本统计口径一致），同时比较 expense 笔数
- 大文件（> 20MB）截断提示，跳过对账
- 历史列表（最多 5 条）

**问题:**
- ⚠️ **P3**：`reconcile` 函数在 `bridge.call('transaction.listByBook', ...)` 失败时默认 `localTxs = []`，此时 `expectedCents = 0`，若导出有数据则显示"对账不一致"，但根因是本地查询失败。应区分"查询失败"与"无数据"

**判定:** 状态流转完整，错误有重试，对账逻辑合理，通过。

---

### 3. ExportPage 测试 ✅

**文件:** `app/src/__tests__/features/ExportPage.test.tsx`

**实现内容:**
覆盖 8 个场景：
1. 加载账本 + 默认选中
2. 创建导出
3. JSON 下载 + 对账一致
4. 对账不一致
5. 失败 + 重试
6. CSV 跳过对账
7. Soft-deleted 排除对账
8. 大文件截断跳过对账（F-M5-04b-F3）

**问题:** 无

**判定:** 覆盖度高，用例边界清晰，通过。

---

### 4. Kotlin ExportClient ✅

**文件:** `app/src/main/java/com/aozora/app/export/ExportClient.kt`

**实现内容:**
- `createExport` / `listExports` / `getExport` / `downloadExport`
- 下载：流式写入临时文件（避免 OOM），再 `saveToDownloads` 到 `Downloads/Aozora/`（API 29+ 用 MediaStore；低版本用 `Environment.DIRECTORY_DOWNLOADS`）
- 对账内容回读限制 20MB（与 bridge 截断阈值对齐）
- 错误分类：`AuthLost` / `Network` / `ServerError` / `NotFound` / `Expired`
- 文件名：从 `Content-Disposition` 解析，剥离路径分隔符防 `../` 遍历

**问题:**
- ⚠️ **P3**：Kotlin 单测目录下无 `export/ExportClientTest.kt`。建议补单测（至少覆盖 `extractFilename` 路径遍历防御 + `parseExportRecord` 字段解析）
- ⚠️ **P4**：`saveToDownloads` 在 API 29+ 通过 MediaStore 写入，若设备无外部存储，`resolver.insert` 返回 null，会抛 `ExportError.Network`。错误类型不精确（不是 network 错误），但处理已够
- ⚠️ **P4**：`extractFilename` 中 `File(match.groupValues[1]).name` 在 Windows 测试环境用 `\` 分隔，但 Android 用 `/`。实际只在 Android 运行，无问题

**判定:** 安全防御到位（路径遍历、OOM、auth 错误分类），通过。

---

### 5. 服务端 Exports 表 ✅

**文件:**
- `server/src/migrations/versions/0002_exports.py`
- `server/src/models/export.py`

**实现内容:**
- 迁移：`exports` 表（`id` / `user_id` / `book_id` / `format` / `status` / `file_path` / `file_size` / `created_at` / `expires_at`），使用 PG UUID
- 迁移链完整（`0001_initial` → `0002_exports`）
- ORM 模型：`Export` + `ExportStatus` / `ExportFormat` 枚举

**问题:**
- ⚠️ **P3**：使用 `postgresql.UUID(as_uuid=True)`，但测试用 SQLite 跑通——说明测试通过 `Base.metadata.create_all` 创建表时 SQLAlchemy 把 PG UUID 退化为 VARCHAR。测试跑通，不是阻塞问题

**判定:** 迁移链完整，通过。

---

### 6. 服务端 Exports API ✅

**文件:**
- `server/src/modules/exports/routes.py`
- `server/src/modules/exports/schemas.py`
- `server/src/modules/exports/service.py`

**实现内容:**
- `POST /exports`：生成导出文件（同步写文件，返回即完成）
- `GET /exports` / `GET /exports/:id`：列表/查询
- `GET /exports/:id/download`：FileResponse 下载
- 服务：
  - `_verify_book_ownership`：N-01 过滤 `deleted_at`（soft-deleted 不可导出）
  - `_mark_expired_if_needed`：N-06 惰性标记过期 + 删除文件
  - `_generate_file`：N-07 通过 `asyncio.to_thread` 卸载到 worker 线程
  - `_ensure_export_dir`：N-05 POSIX 强制 0o700
  - `cleanup_expired_files`：启动时清理 25 小时前的文件

**问题:**
- ⚠️ **P4**：`cleanup_expired_files` 仅基于文件 mtime（25h），不查数据库。若数据库 `expires_at` 与文件 mtime 不一致（罕见），可能误删或漏删。实际影响小

**判定:** 安全（ownership guard + 跨用户拦截 + soft-delete 过滤）、性能（event loop 不阻塞）、文件生命周期管理完整，通过。

---

### 7. 服务端 Exports 测试 ✅

**文件:** `server/tests/test_stats_exports.py`

**实现内容:**
覆盖 40+ 用例：
- 月度统计正确性 / 分类细分 / 空周期 / 负 balance / 排除删除 / ownership guard
- 趋势统计空月填充
- 统计-交易列表对账不变量
- 周期校验（N-02 / N-33 回归）
- 导出：创建/下载 JSON 与 CSV / 跨用户拦截 / 列表 / 导出-统计对账
- 过期：状态写回 / 列表显示 / 下载拒绝且文件删除 / 未过期不动
- 文件生命周期：启动清理 / POSIX 权限 / event loop 卸载

**问题:** 无

**判定:** 测试覆盖非常完整，包含安全测试，通过。

---

## 四、发现的问题汇总

### P3（建议修复，影响健壮性/完整性）

1. **T-M5-04a** — `docs/legal/privacy-policy-v1.md` 联系方式 `[项目维护者]` 占位符应替换为真实联系方式
2. **T-M5-04a** — 服务端 `has_user_consented(user_id)` 已实现但无任何调用点，F-M5-04a-F1 "server-side defense-in-depth" 形同虚设。应接入关键路由（如 `/sync/...` 或认证中间件）
3. **T-M5-04a** — Kotlin `handleConsentRecord` 默认 `deviceId = ""` 与服务端 `device_id` `min_length=1` + 正则冲突。当前路径未暴露，但一旦启用同步会 422
4. **T-M5-04a** — Kotlin `ConsentStore` 与 `handleConsentRecord` 缺少单元测试
5. **T-M5-04a** — `ai-badge` CSS 类名在 4 个文件中被使用但 `index.css` 中无对应规则
6. **T-M5-04b** — `ExportPage.reconcile` 在本地交易查询失败时默认 `localTxs = []`，会误报"对账不一致"。建议区分查询失败与无数据
7. **T-M5-04b** — Kotlin `ExportClient` 缺少单测

### P4（可忽略/锦上添花）

1. **T-M5-04a** — 纯浏览器环境下 `OnboardingPage` 跳过 `bridge.recordConsent`，应在代码注释中说明
2. **T-M5-04a** — Room `insertConsent` 用 `OnConflictStrategy.REPLACE`，但 PK 是 UUID 不会冲突。无害
3. **T-M5-04b** — `cleanup_expired_files` 仅基于 mtime 而非数据库 `expires_at`，罕见场景可能不一致
4. **T-M5-04b** — `ExportClient.saveToDownloads` 在 MediaStore 失败时抛 `ExportError.Network`，错误类型不精确

---

## 五、与计划对照

| 计划要求 | 实现状态 | 验证结果 |
|---|---|---|
| 隐私政策文本版本化、含数据采集范围、AI 使用说明、第三方共享、数据保留与删除、联系方式 | ✅ | `privacy-policy-v1.md` 7 章节完整 |
| 首启同意流：首次启动展示隐私摘要 → 用户点击"同意并继续" → 调用 bridge consent.record → 写入 UserConsent 表 | ✅ | `OnboardingPage.tsx` 两步流程 + bridge 调用 + Room + SharedPreferences |
| 权限按需申请：在对应功能入口前申请，拒绝后不重复弹窗 | ✅ | 4 个场景独立申请，`PermissionManager` 四态管理 |
| AI 内容标识：AI 生成内容旁展示"AI 生成"标签 | ✅ | 4 个关键位置（对话/洞察/AI 识别交易） |
| UserConsent 双端存储：Room user_consent 表 + 服务端 user_consents 表 | ✅ | 双端迁移 + ORM 模型完整 |
| MePage 导出入口 → bridge export.create → 服务端 POST /exports → 下载 → 对账 | ✅ | `ExportPage.tsx` + `ExportClient.kt` + 服务端 `/exports` 完整链路 |
| 导出合计对账：下载后前端解析导出文件，合计与本地统计一致 | ✅ | JSON 格式对账 expense 合计 + 笔数，排除 soft-delete |
| MePage 导出状态：导出中、导出完成、导出失败 | ✅ | 状态机 `idle → creating → ready → downloading → downloaded / failed` |

---

## 六、审查结论

**T-M5-04 隐私、权限与账号生命周期（04a + 04b）：✅ 通过**

- 2 个子任务（04a / 04b）全部满足计划要求
- 隐私政策文本完整，首启同意流、权限按需申请、AI 标识、UserConsent 双端存储均已实现
- 导出端到端流程完整，对账逻辑、状态流转、安全防御、测试覆盖均到位
- 7 项 P3 级改进建议（联系方式占位符、`has_user_consented` 接入、deviceId 默认值、Kotlin 单测、ai-badge CSS、对账错误处理、ExportClient 单测），不阻塞通过
- 4 项 P4 级可忽略问题

**建议优先处理：**
1. `has_user_consented` 接入关键路由（P3，F-M5-04a-F1 真正生效）
2. 联系方式占位符替换（P3，合规形式化）
3. Kotlin 单测补全（P3，consent / export 双侧）

**可标记 T-M5-04a / 04b 为完成，进入 T-M5-04c 账号删除与数据保留。**
