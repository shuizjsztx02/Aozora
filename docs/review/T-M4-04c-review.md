# T-M4-04c 代码审查（服务端附件 API）

审查日期：2026-09-06（第 1 轮）
审查范围：`server/src/modules/attachments/`（routes/service/schemas/__init__）、`server/src/models/attachment.py`、`server/src/migrations/versions/0009_attachment_user_id.py`、`server/src/common/config.py`（upload_dir）、`server/src/main.py`（router 注册 + 启动清理）、`server/tests/test_attachments.py`、`server/tests/test_migrations.py`（0009 部分）、T-M4-04c 完成记录。
基线：04c 文件集随提交 `c101afb` 入库，审查时与 HEAD（`3b73dd5`）一致（`git status` 对该文件集为空）。工作区含并行会话的大量未提交改动（T-M4-03 第 4 轮修复、T-M4-05 修复、04a P2 修复、04d 初期文件），均不在本次审查范围，本审查未触碰。
审查环境：Windows / Python 3.13.12 / pytest + aiosqlite（内存库）；CI 事实源为 `.github/workflows/ci.yml` server job（ubuntu / py3.13 / `ruff check src/ tests/` + `pytest tests/`）。
编号说明：R-M4-38 已被并行的 T-M4-03 第 4 轮复审占用，本轮自 R-M4-39 起。

## 结论

**⚠️ 审查不通过（第 1 轮）：1 项 P1 + 4 项 P2 + 2 项 P3。**

ownership 隔离、过期翻转、link 语义等核心实现与测试覆盖扎实（28/28 + 13/13 实测通过，ruff 04c 范围干净），但：启动清理与「LINKED 不过期」的模块自身承诺直接矛盾（R-M4-39，P1，持久化部署下重启即丢已链接票据文件）；对外声称的「magic bytes 防 Content-Type 伪造」实际不生效（R-M4-40）；上传大小限制滞后于全量读取（R-M4-41）；文档先行规则未遵循（R-M4-42）；迁移 0009 的 SQLite 兼容声明经探针证伪且全仓迁移从无执行验证（R-M4-43）。建议修复 R-M4-39/40/41 并补齐架构文档后复审。

## 门禁复核（审查时实测）

| 门禁 | 完成记录声称 | 实测 |
|---|---|---|
| `pytest tests/test_attachments.py` | 28 passed | ✅ 28 passed（1.74s） |
| `pytest tests/test_migrations.py` | 链式接续 0008 | ✅ 13 passed（静态链检查） |
| `pytest tests/` 全量 | 397 passed + 1 项环境 error | ⚠️ 403–404 passed、1 skipped（POSIX-only 用例）、1 error（`test_stats_exports` Windows tmp 权限，与完成记录披露同类的环境问题）。两次运行数字不同：工作区含并行会话未提交改动，全量数字与交付时点不可比；04c 范围文件未受影响 |
| `ruff check`（CI 等价） | 未声称 | ✅ 04c 文件集 0 违规；全仓 `src/ tests/` 现有 1 处 I001 位于 `tests/test_narrative_report.py`（并行 T-M4-03 未提交改动，非 04c 范围） |
| alembic 迁移执行 | 未声称（仅声称链接续） | ❌ 全仓测试/CI 从不执行 `alembic upgrade`（仅静态源码检查）。探针实测：SQLite 全链在 0004 失败（既有、与 04c 无关）；**0009 自身 DDL 在 SQLite 亦不兼容**（见 R-M4-43）。PostgreSQL 执行未演练（审查环境无 PG 实例，环境限制，与 T-M2 审查披露一致） |
| 远端 CI | 未声称 | ❌ 推送后已确认失败（本审查提交 `563e0d6` 触发 run 34038442528）：**Server job 在 conftest import 阶段即挂、0 个测试被执行**——CI 全新安装拉到新版 starlette，其 testclient 弃用警告（"install httpx2 instead"）被 `pyproject.toml` 的 `filterwarnings=["error"]` 升级为错误，pytest exit 4。属依赖漂移的 CI 基础设施问题（starlette 仅经 `fastapi>=0.115,<1` 间接约束、未锁定；本地 starlette 1.6.0 无此警告，故本地 41/41 通过），先于且独立于 04c 交付。Android job 亦失败（并行会话 CI 修复进行中，非 04c 范围）；Client job ✅。04c 的「28 测试全绿」仅本地可复核，远端验证被基础设施失败阻断 |

## 问题清单

| ID | 级别 | 类别 | 状态 | 摘要 |
|---|---|---|---|---|
| R-M4-39 | 🔴 P1 | 正确性/可靠性 | ⬜ 未修复 | 启动清理不查 DB、按 mtime 全量删文件——LINKED/PARSED 附件文件在重启后被误删；docstring 声称删 DB 行实际从不碰 DB |
| R-M4-40 | 🟡 P2 | 安全 | ⬜ 未修复 | MIME「防伪造」拒绝分支恒假（死代码），任意内容配 image/* 头即可入库；完成记录安全声称不实、测试名误导 |
| R-M4-41 | 🟡 P2 | 安全/可靠性 | ⬜ 未修复 | `upload.read()` 全量物化后才检查 10MB 上限，超大上传可耗尽内存/临时盘；仓库无代理层 body limit |
| R-M4-42 | 🟡 P2 | 流程/文档 | ⬜ 未修复 | 文档先行未遵循：docs/architecture/ 全套无 attachments 只言片语（data-model 无表条目、security 无上传存储面） |
| R-M4-43 | 🟡 P2 | 正确性/测试 | ⬜ 未修复 | 迁移 0009 docstring 声称 SQLite 兼容，探针证伪（add_column 内联 FK → NotImplementedError）；迁移在全仓从无执行验证 |
| R-M4-44 | 🟢 P3 | 正确性 | ⬜ 未修复 | link 的交易 ownership 校验未过滤软删 Book（与 exports N-01 先例不一致） |
| R-M4-45 | 🟢 P3 | 代码卫生/测试 | ⬜ 未修复 | schemas 死枚举、get_attachment 重复 expiry flip、test_link_expired 注释误导、GET /attachments 无分页 |

---

## 问题详情

### R-M4-39 启动清理误删 LINKED/PARSED 附件文件，且从不清理 DB 行

- **ID：** R-M4-39
- **级别：** P1
- **类别：** 正确性 / 可靠性
- **状态：** 未修复
- **文件与位置：** `server/src/modules/attachments/service.py` L73-100（`cleanup_expired_files`）、L74（docstring）、L8-10（模块 docstring 承诺）、`server/src/main.py` L54-63（启动调用）
- **问题与触发条件：** `cleanup_expired_files()` 对 `upload_dir` 做 `rglob("*")`，凡 mtime 超过 25h 的文件一律 `unlink`，**全程不查询数据库**。而模块自身承诺「Linked (LINKED) or parsed (PARSED) attachments do not expire until a future task (T-M5-04c)」（service.py L8-10），link 后 `expires_at` 也被清空（L324）。触发条件：配置持久化 `UPLOAD_DIR`（config.py L104 明文推荐 "Override via UPLOAD_DIR for persistent deployments"）+ 附件链接后服务重启时文件龄期 > 25h → 文件被启动清扫删除，DB 行仍为 LINKED，下载返回 404 `file_missing`，用户票据图片永久丢失。开发机（Windows %TEMP% 持久）重启即可复现。
- **证据或复现命令：** 代码事实：L84-91 无任何 DB 访问、无状态过滤；`tests/test_attachments.py::TestCleanup` 仅覆盖无 DB 行的孤儿文件，未覆盖「LINKED 行 + 老 mtime 文件」场景。逻辑推演：LINKED 文件 mtime = 上传时刻，25h 后任何一次重启必删。（审查模式只读，未在工作区构造运行时复现。）
- **影响：** 违背本任务自身的保留承诺与完成记录「`LINKED` 状态不过期」声称；D-EXEC-05 落地前的唯一持久化缓解手段（UPLOAD_DIR）被此缺陷反噬——比不配置更糟（配置了持久卷仍丢数据）。附带缺陷：docstring 声称 "Delete files **and DB rows**"（L74）但实现从不碰 DB，EXPIRED 行永不清理，attachments 表只增不减。
- **修复建议：** 清扫改为 DB 驱动：① 查询已过期 PENDING/EXPIRED 行，删除其 `object_key` 文件并删除（或按策略归档）DB 行；② 磁盘上无对应 DB 行且超过宽限期的孤儿文件（上传中断产物）删除；③ **绝不删除**被 LINKED/PARSED 行引用的文件。同步修正 docstring。
- **复验方法：** 新增回归测试：创建 LINKED 附件 + `os.utime` 把 mtime 调老 26h → `cleanup_expired_files()` 不删该文件；EXPIRED 行 + 老文件 → 文件与行均被清理；孤儿老文件 → 删除。

### R-M4-40 「magic bytes 防 Content-Type 伪造」不生效——拒绝分支恒假

- **ID：** R-M4-40
- **级别：** P2
- **类别：** 安全
- **状态：** 未修复
- **文件与位置：** `server/src/modules/attachments/service.py` L203-211（sniff 检查）、L334-345（`_sniff_mime`）；`server/tests/test_attachments.py` L219-234；完成记录 L638/L663/L670
- **问题与触发条件：** `_sniff_mime` 的返回值域为 `{image/jpeg, image/png, image/webp, None}`，是 `ALLOWED_MIME_TYPES ∪ {None}` 的子集，因此 `if sniffed and sniffed not in ALLOWED_MIME_TYPES` **恒为假，是不可达死分支**。任意字节内容（PDF、EXE、HTML……）只要请求头声明 `image/jpeg|png|webp`，即以该 MIME 入库存储（sniff 返回 None → `final_mime` 回退到头）。嗅探的实际作用仅剩「头声明 image/jpeg 但体是 PNG magic 时纠正存储 MIME」，从不拒绝。
- **证据或复现命令：** `tests/test_attachments.py::TestUploadSuccess::test_rejects_spoofed_header_when_body_is_not_image`——**测试名声称 rejects，断言的却是上传成功**（L233-234 `att.mime_type == "image/jpeg"`），把放行行为钉成了契约；`test_unknown_magic_bytes_falls_back_to_header` 同样确认放行。完成记录 L638「magic bytes 嗅探兜底防 Content-Type 伪造」与 L670 安全声称与代码事实不符（L663「行为已文档化」措辞含糊）。
- **影响：** 计划表验收项「大小与**格式**限制；限制与策略断言」只在头层面成立，内容层面无效。下游 T-M4-04e 会把未经验证的内容送入多模态模型（浪费配额、潜在 prompt-injection 载体）；存储层可能滞留任意文件类型。下载侧 `application/octet-stream` + 服务端生成文件名缓解了 XSS（见「确认无问题」），故不升 P1。
- **修复建议：** 三种允许格式的 magic bytes 均为确定性签名，sniff 返回 `None` 时应直接 415（拒绝无法识别为允许格式的内容），删除恒假分支；更名/改写上述两个测试为断言拒绝；完成记录安全声称同步改写。若产品上需要宽容（截断图片等），至少在记录中如实降级声称并让 04e 入口再做真实图像解码验证。
- **复验方法：** 新回归用例：`NOT_IMAGE` 字节 + `image/jpeg` 头 → 415；真 JPEG/PNG/WEBP magic → 201；PNG magic + jpeg 头 → 201 且 mime 纠正为 image/png。

### R-M4-41 上传大小检查滞后于全量读取——内存/临时盘 DoS 面

- **ID：** R-M4-41
- **级别：** P2
- **类别：** 安全 / 可靠性
- **状态：** 未修复
- **文件与位置：** `server/src/modules/attachments/service.py` L193-199（`data = await upload.read()` 后才 `len(data) > MAX_UPLOAD_BYTES`）
- **问题与触发条件：** 请求体被**完整物化为单个 bytes 后**才检查 10MB 上限。Starlette 的 UploadFile 超过 1MB 会 spool 到磁盘，但 `read()` 会把全部内容拉回内存——攻击者（或故障客户端）发送数 GB 的 multipart 体：① spool 阶段填满临时盘；② `read()` 阶段占用等量 RAM，少量并发即可 OOM。仓库内无任何请求体大小限制中间件（`src/common/` 无此模块），`infra/` 亦无 `client_max_body_size` 等代理配置。
- **证据或复现命令：** 代码事实（L194 一次性 `read()`）；`grep -rn "client_max_body_size\|MAX_BODY\|ContentSize" server/ infra/` 无命中。集成测试 `test_oversized_rejected` 只验证 10MB+1 的良性拒绝，不覆盖超大体的资源行为。
- **影响：** 认证用户即可触发的资源耗尽向量（guest token 可自助获取，门槛极低）。生产部署若前置代理限体则缓解，但该防线目前不存在于仓库任何配置。
- **修复建议：** 分块读取（如 64KB/块）累计至 `MAX_UPLOAD_BYTES + 1` 即中止并 413，不落盘、不物化全量；同时在部署文档/infra 清单登记代理层 body limit 为必备项（归 D-EXEC-05/T-M5 部署面）。
- **复验方法：** 单测：mock UploadFile 分块产出 2×MAX 数据 → 413 且读取字节数 ≤ MAX+块大小；集成测：大文件请求被拒且服务内存无等量增长（可用 `tracemalloc` 粗断言）。

### R-M4-42 文档先行未遵循：架构文档完全没有 attachments

- **ID：** R-M4-42
- **级别：** P2
- **类别：** 流程 / 文档
- **状态：** 未修复
- **文件与位置：** `docs/architecture/data-model.md`、`docs/architecture/security.md`、`docs/architecture/overview.md`（均无 attachment 内容）；对照 AGENTS.md 关键工程约束「架构/协议/数据结构变更必须先改对应文档再动代码」
- **问题与触发条件：** 04c 变更了数据结构（attachments 表 +user_id/+file_size + 状态机语义）并新增 6 端点 API 与文件存储安全面，但 `grep -n -i "attachment" docs/architecture/*.md` 零命中；exports（同类文件产物功能）在 data-model.md 有条目。完成记录「修改的文档」仅列执行计划自身。attachments 表条目缺失自 M2 已存在（先前缺口），但 04c 是首次把该表变成带真实 API 的生产功能，属应补文档而未补。
- **证据或复现命令：** 上述 grep；完成记录 L650-658。
- **影响：** 后续任务（04d/04e、T-M5-04c 生命周期、D-EXEC-05 对象存储迁移）没有权威的数据模型/API/安全参考，只能读代码反推；违背仓库治理规则。
- **修复建议：** data-model.md 补 attachments 表（含 user_id/file_size、PENDING→LINKED/PARSED/EXPIRED 状态机、保留策略）；API 契约文档补 6 端点；security.md 补上传存储面（白名单、限制、目录权限、ownership 模型、已知残留风险）。
- **复验方法：** grep 命中 + 文档评审；与实现逐项对照（状态机、限制值、端点清单）。

### R-M4-43 迁移 0009 的 SQLite 兼容声明不实；迁移在全仓从无执行验证

- **ID：** R-M4-43
- **级别：** P2
- **类别：** 正确性 / 测试
- **状态：** 未修复
- **文件与位置：** `server/src/migrations/versions/0009_attachment_user_id.py` L15-18（docstring 声称）、L33-41（add_column 内联 FK）；`server/tests/test_migrations.py`（仅静态源码检查）
- **问题与触发条件：** docstring 声称「SQLite compatibility: both columns are added as nullable to satisfy SQLite's ALTER TABLE limitation」。探针实测：在 SQLite 上执行 0009 的 `op.add_column(..., sa.ForeignKey(...))` 抛 `NotImplementedError: No support for ALTER of constraints in SQLite dialect`（alembic `ddl/sqlite.py:78`，经 `toimpl.py:204`）——阻塞点是**内联 FK**而非 nullability，nullable 选择对 SQLite 兼容毫无帮助，声明因果不实。同时全仓测试与 CI 从不执行 `alembic upgrade`（test_migrations.py 仅 import 模块做正则/链检查），0009 在任何后端都未被执行验证过。
- **证据或复现命令：** 审查探针（临时 SQLite + alembic Operations 直接执行 0009 DDL）：`NotImplementedError` 必现；全链探针在 0004 已先行失败（`op.drop_constraint`，既有问题、非 04c 引入，说明 SQLite 迁移执行路径早已不通）。PostgreSQL 上 ADD COLUMN + ADD CONSTRAINT 为标准 DDL，**推断**可执行，但审查环境无 PG 实例，未演练（环境限制，与 T-M2 审查「无 PG 实例」披露一致；既有 R-M2-16 为同类后端分叉的文档化先例）。
- **影响：** ① 0009 的交付声称「Alembic 迁移（为 SQLite 兼容列 nullable）」的括号内容不成立，误导后续维护者（如有人想恢复 SQLite 迁移可执行性会信此声明）；② 生产首次部署前，0009 乃至全链从未在任何数据库真实跑通过（CI 不覆盖），部署即首跑。
- **修复建议：** 最小改：修正 0009 docstring，如实声明「仅 PostgreSQL 可执行；SQLite 需 batch mode」（与 0004 现状一致）。系统性（可挂账 T-M5-05a/staging）：CI 或 staging 增加 PG 容器上的 `alembic upgrade head` 演练 job。
- **复验方法：** docstring 与探针结果一致；（若做系统项）CI job 在 PG 上跑通全链并有 downgrade 演练。

### R-M4-44 link 的交易归属校验未过滤软删 Book

- **ID：** R-M4-44
- **级别：** P3
- **类别：** 正确性
- **状态：** 未修复
- **文件与位置：** `server/src/modules/attachments/service.py` L129-141（`_verify_transaction_ownership`）
- **问题与触发条件：** 查询过滤了 `Transaction.deleted_at.is_(None)` 与 `Book.owner_id == user_id`，但未过滤 `Book.deleted_at`（Book 带 SoftDeleteMixin，ledger.py L41）。软删账本内的未软删交易仍可被 link 附件。对照先例：exports 的 `_verify_book_ownership` 在 N-01 修复中明确加入 `Book.deleted_at.is_(None)`（exports/service.py L88-98）。
- **证据或复现命令：** 两处代码对照；无现有测试覆盖「软删账本 + link」组合。
- **影响：** 非跨用户泄露（owner 校验在），属生命周期口径不一致：对已删除账本的交易继续挂接数据，未来恢复/清理语义含糊。
- **修复建议：** join 条件补 `Book.deleted_at.is_(None)`，加一条回归用例（软删账本 → link 404）。
- **复验方法：** 新用例通过；既有 link 用例不回归。

### R-M4-45 代码卫生与测试表述杂项

- **ID：** R-M4-45
- **级别：** P3
- **类别：** 代码卫生 / 测试
- **状态：** 未修复
- **文件与位置与明细：**
  1. `schemas.py` L12-16：`AttachmentStatus` 枚举定义后无任何使用（`AttachmentResponse.status` 为 `str`，routes 不 import），与 `models/attachment.py` 的同名枚举重复——死代码。
  2. `service.py` L257-262：`get_attachment` 里 `_get_owned`（L126）已做 expiry flip，又调一次 `_mark_expired_if_needed`——无害冗余。
  3. `tests/test_attachments.py` L417-420：`test_link_expired_returns_410` 注释声称 link 看不到过期翻转、需先经 download 触发——与代码事实不符（`link_attachment` 自身经 `_get_owned` 就会翻转并 410），测试多绕了一步且注释误导后续维护者。
  4. `routes.py` L33-40：`GET /attachments` 无分页，而 accounts/books/categories/transactions 列表端点均使用 `src/common/pagination`——与仓库模式不一致，附件量增长后劣化。
- **影响：** 均不影响当前正确性；累积维护成本与误导风险。
- **修复建议：** 删死枚举；去重复 flip；修正测试注释（可顺带直接断言 link 首调即 410）；分页可挂账到 04d 客户端接线时一并做。
- **复验方法：** ruff/pytest 全绿；grep 无 `schemas.AttachmentStatus` 引用残留。

---

## 完成记录声称核实（通过项）

| 声称 | 核实 |
|---|---|
| 6 端点（upload/list/get/download/delete/link） | ✅ routes.py 恰好 6 个，方法/路径/状态码（201/200/204）一致 |
| 28 项测试 | ✅ 实测 28 passed；分类计数与记录逐项吻合（4+4+3+3+2+1+4+1+1+5） |
| 10MB → 413 | ✅ 服务层 + HTTP 层双测试（但检查时机见 R-M4-41） |
| MIME 白名单 → 415 | ✅ 头层面成立（内容层面见 R-M4-40） |
| ownership：跨用户 get/list/download/delete → 404 四处一致 | ✅ 服务层 3 用例 + HTTP 层 test_cross_user_isolation；不泄露存在性 |
| 全端点 Bearer | ✅ 6 路由均 `Depends(get_current_user)`；test_unauthenticated_rejected 401/403 |
| PENDING 24h → EXPIRED + 删文件 + 下载 410 | ✅ TestExpiry/TestDownload 断言完整（status、磁盘、HTTP 码三重） |
| link：成功清 expires_at / 已链接 409 / 过期 410 / 跨用户交易 404 | ✅ TestLink 4 用例 |
| 启动清理注册 | ✅ main.py lifespan，try/except 包裹不阻塞启动（清扫语义见 R-M4-39） |
| `Settings.upload_dir` + 临时目录兜底 | ✅ config.py，默认 /tmp/aozora_uploads，UPLOAD_DIR 可覆盖 |
| 对象键 `<user_id>/<attachment_id>.<ext>` | ✅ service L216，扩展名来自固定映射 |
| 上传目录 0o700（POSIX） | ✅ `_ensure_upload_dir` chmod + OSError 降级日志，Windows 分支正确跳过 |
| 迁移链式接续 0008 | ✅ test_migrations 静态断言 down_revision/revision（执行验证见 R-M4-43） |
| user_id ORM NOT NULL / 迁移 nullable（R-M2-16 模式） | ✅ 与既有先例一致，记录如实披露 |
| 回退方式 | ✅ 纯新增模块 + 增量迁移，回退路径成立（限部署前；已跑迁移后删文件会断链，记录未注明，轻微） |

## 确认无问题的设计点

- **无路径遍历面**：`object_key` 完全由服务端生成（uuid + `_MIME_TO_EXT` 固定映射），`final_mime` 恒在白名单内，用户可控文件名不参与存储路径；下载路径 = `upload_dir / object_key`，DB 为唯一来源。✅
- **下载安全**：`FileResponse(media_type="application/octet-stream", filename=服务端生成)`——无存储型 XSS、无 Content-Disposition 头注入（原始 filename 不落库不回显）。✅
- **时区处理**：`expires_at` naive/aware 双兼容（`tzinfo is None → replace(UTC)`），SQLite 返回 naive 不炸。✅
- **清理幂等**：`unlink(missing_ok=True)` + OSError 捕获，惰性翻转并发安全（双请求同翻无害）。✅
- **惰性过期翻转**：`_get_owned` 统一入口翻转，link/delete/download 观察到一致状态；GET list 带写副作用属已声明设计。✅
- **FK 语义**：user CASCADE（用户删→附件行删）、transaction SET NULL（交易删→附件保留待重链）合理。✅
- **404 不泄露存在性**：跨用户与不存在同码同文案。✅
- **测试隔离**：`tempfile.mkdtemp` 自建 fixture 规避 Windows pytest tmp_path 权限坑（与全量套件中 test_stats_exports 的环境 error 对照，选择正确）。✅
- **link 允许 PARSED 状态**：仅拒 EXPIRED/LINKED，为 04e「解析后链接」预留，语义合理。✅

## 观察项（不编号，不计入门禁）

1. `AttachmentResponse` 回显 `object_key` 与 `user_id`：当前仅属主可见、内容皆为其已有信息，无实害；D-EXEC-05 迁移 S3 后 object_key 暴露存储布局，建议届时收敛响应字段。
2. 并发 link 同一附件存在 last-write-wins 竞态（无状态翻转的原子守卫）：单用户场景影响可忽略，04e 批量流程若引入自动 link 可再评估。
3. upload 先写文件后落 DB 行：DB 失败留下孤儿文件，靠启动清扫回收——修 R-M4-39 时须保留「无 DB 行孤儿文件」的回收语义（已写入修复建议）。
4. 无上传频率/配额限制：记录已如实挂账 T-M5-02（每用户存储配额），不重复立项。
5. `cleanup_expired_files` 在 lifespan 内同步执行，海量文件时拖慢启动：当前规模无碍，T-M5-05a 补 cron 时一并异步化。

## 审查中改动的文件

按 Review 模式只读边界，本次未修改任何项目代码/测试/配置。产出与状态更新仅限：

1. `docs/review/T-M4-04c-review.md`（本文档，新增）
2. `docs/milestones/production-execution-plan.md`（仅 04c 相关状态行与完成记录横幅；该文件工作区中并行会话的未提交改动未被本次提交纳入）

## 下一步建议

- **Bug-fix 模式**处理 R-M4-39（P1）与 R-M4-40/41（P2，安全面），R-M4-42 文档补齐可同轮；R-M4-43 最小改 docstring、PG 演练挂账 staging；R-M4-44/45 随手修或挂账 04d。
- 修复后复审重点：清扫回归测试（LINKED 不删）、sniff 拒绝分支真实生效、分块读取上限、CI 全量绿。
- **远端 CI 前置阻塞**：server job 的 starlette 弃用警告失败（依赖漂移）需先由 CI 修复线处理（锁定 starlette 版本或调整警告过滤），否则 04c 修复复审的远端门禁无从运行。归属并行 CI 修复会话/独立 bug-fix 任务，不在本审查只读边界内实施。
- 04b（Vosk 离线语音）仍为 04a 占位（`voice.*` 返回 unavailable），未实现、未在本次范围。
