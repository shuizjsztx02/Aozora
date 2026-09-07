# T-M4-04 第五轮复审修复报告

**日期：** 2026-09-07
**状态：** ✅ 全部修复完成

## 修复清单

| 编号 | 级别 | 问题 | 修复方式 | 验证 |
|------|------|------|----------|------|
| NEW-1 | 🟡 Medium | `attachments.user_id` 缺数据库索引，所有查询依赖此列过滤，会退化为全表扫描 | 新增迁移 `0010_attachment_user_id_backfill` 创建 `ix_attachments_user_id` 索引 | `test_migrations.py::test_attachment_backfill_migration_chains_onto_0009` 通过 |
| NEW-2 | 🟢 Low | 孤儿文件清理缺口：上传流程先写文件再 flush DB，若 DB 失败则文件残留 | `service.py:upload()` 用 try/except 包裹 `db.flush()`，失败时 `dest.unlink(missing_ok=True)` | `test_attachments.py::TestOrphanCleanup::test_upload_db_failure_cleans_up_file` 通过 |
| Bug-C1 | ⚠️ 生产阻塞 | 迁移 0009 留 TODO：生产部署前需 backfill `user_id` 并收紧 NOT NULL | 迁移 0010 执行：(1) UPDATE 回填 user_id from transactions；(2) DELETE 孤儿 PENDING 行；(3) ALTER user_id SET NOT NULL | 迁移链测试通过 |
| 执行计划状态 | 📝 文档 | `production-execution-plan.md` 与实际代码脱节 | 更新 T-M4-04c/d/e 状态：04c ✅ 通过；04d ✅ 通过；04e ✅ 通过 | 文档已更新 |
| 前端测试缺口 | 🧪 测试 | QuickEntrySheet/ImagePickerSheet/ImageUploadView 无组件测试 | 新增 28 项组件测试覆盖核心路径 | 480 前端测试全绿 |

## 修改文件

### 后端（server/）

| 文件 | 改动 |
|------|------|
| `src/migrations/versions/0010_attachment_user_id_backfill.py` | **新增**。backfill user_id、删除孤儿、收紧 NOT NULL、创建索引 |
| `src/migrations/versions/0009_attachment_user_id.py` | 更新 TODO 注释为 RESOLVED，指向 0010 |
| `src/modules/attachments/service.py` | `upload()` 增加 try/except 包裹 db.flush()，失败时删除孤儿文件 |
| `tests/test_migrations.py` | 新增 0010 到迁移链测试；新增 `test_attachment_backfill_migration_chains_onto_0009` |
| `tests/test_attachments.py` | 新增 `TestOrphanCleanup` 类测试孤儿文件清理 |

### 前端（app/src/__tests__/）

| 文件 | 改动 |
|------|------|
| `features/ImagePickerSheet.test.tsx` | **新增**。8 项测试：渲染、权限预检、自动续接、取消、设置引导 |
| `features/ImageUploadView.test.tsx` | **新增**。14 项测试：上传中/等待/成功/失败四态、文件大小格式化、错误码映射 |
| `features/QuickEntrySheet.test.tsx` | **新增**。6 项测试：渲染、收支切换、图片上传状态展示、附件按钮 |

### 文档（docs/）

| 文件 | 改动 |
|------|------|
| `milestones/production-execution-plan.md` | 更新 T-M4-04 整体状态和子任务 04c/d/e 状态；更新 04c 完成记录复审结论 |

## 测试结果

| 端 | 命令 | 结果 |
|----|------|------|
| 服务端 | `pytest tests/ -q` | **426 passed, 2 skipped**（原有 424 + 新增 2：迁移链 + 孤儿清理） |
| 前端 | `npm test -- --run` | **480 passed**（原有 452 + 新增 28） |
| 类型检查 | `npm run typecheck` | **通过** |
| 代码检查 | `npm run lint` | **通过**（仅有预先存在的 warnings） |

## 迁移使用说明

生产部署前需按顺序执行迁移：

```bash
# 0009 已在 T-M4-04c 阶段执行（nullable user_id + file_size）
# 0010 需在生产部署前执行
alembic upgrade 0010_attachment_user_id_backfill
```

0010 会：
1. 回填存量 `attachments` 行的 `user_id`（从关联的 `transactions.user_id`）
2. 删除无 `user_id` 且无 `transaction_id` 的孤儿 PENDING 行
3. 收紧 `user_id` 为 NOT NULL
4. 创建 `ix_attachments_user_id` 索引

回滚：`alembic downgrade 0009_attachment_user_id`（索引删除 + user_id 回退 nullable）。

## 下一步建议

1. 📱 **真机验证**：语音录音 → NLU 闭环（T-M4-04b 遗留）
2. 🔍 **D-EXEC-05**：对象存储正式接入（当前仍走临时目录）
3. 🧪 **E2E**：T-M5-01d 端到端核心路径验证
