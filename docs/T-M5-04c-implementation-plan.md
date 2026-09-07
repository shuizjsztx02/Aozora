# T-M5-04c 账号删除与数据保留 — 实现计划

## 范围

### 服务端
1. Schema 变更
   - `users` 增加 `frozen_at`、`deletion_requested_at` 列
   - `UserStatus` 新增 `FROZEN = "frozen"`
   - `attachments` 增加 `keep_after_parse` 布尔列（默认 false）
   - 新建 `deletion_audit` 表（`user_id_hash`、`requested_at`、`completed_at`、`deleted_tables`、`rows_count`）
   - 新建 `deletion_tokens` 表（短生命周期 confirm_token 存储）
2. API
   - `POST /me/deletion-request`：签发 5 分钟有效的 confirm_token
   - `DELETE /me?confirm_token=xxx`：校验 token 后把用户置为 frozen，记录 `deletion_requested_at`
   - `GET /me`：返回 `frozen_at` 与 `deletion_requested_at`，客户端可显示倒计时
3. 冻结期行为
   - `/sync/push` → 403 `account_frozen`
   - `/sync/pull` → 空响应（不 403，保留客户端可重试形态）
   - `/stats/*` → 排除 frozen 用户的数据
   - `get_current_user` 依然放行 frozen 用户（否则无法调用 GET /me）
4. Cron 脚本
   - `infra/cleanup/account-deletion.py`：扫描 `users.frozen_at <= now - 7d` 的用户 → 物理删除 → 写 `deletion_audit`
   - `infra/cleanup/attachment-expiry.py`：扫描 `attachments.status='parsed' AND keep_after_parse=false AND updated_at <= now - 24h` → 删除磁盘文件 → 清 object_key 标记为 expired

### 客户端
1. `AccountClient.kt` 新增 `requestDeletionToken()` 与 `deleteAccount(confirmToken)`
2. `MePage.tsx` 在"更多"分组末尾新增"删除账号"按钮
3. 二次确认对话框（输入 "DELETE" 以确认）
4. 成功后清空 Room（`clearAllTables()`）+ TokenStore（`clear()`）→ 跳回 Onboarding 页

## 验证
- 服务端 pytest：冻结期 push 返回 403，pull 返回空，stats 不包含 frozen 用户；deletion token 过期后无法使用；7 天 cron 物理删除后 `deletion_audit` 有完成证明
- 客户端：UI 测试覆盖二次确认对话框；集成测试验证删除后 Room 行数为 0、TokenStore 为空
- 端到端：全新注册 → 写入流水 → 触发删除 → 冻结期内 push 失败 → 7 天后物理删除完成 → 客户端清空
