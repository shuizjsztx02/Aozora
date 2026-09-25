# ADR-014 分类图标两端化，跨端只承载 emoji

**状态：** 已采纳（2026-09-25）
**范围：** 第十九轮 REQ-73（T19-1 地基 / T19-2 放开 AI），属 **L2 变更**（数据模型 + 同步契约；不涉 UI 交互改造）
**关联文档：** `docs/architecture/data-model.md`（`categories` 表一节）；`server/src/models/ledger.py`；`server/src/migrations/versions/0023_category_icon.py`；`server/src/modules/sync/service.py`；`app/src/main/java/com/aozora/app/db/Repository.kt`；`app/src/main/java/com/aozora/app/sync/SyncModels.kt`；`server/tests/test_sync_cloud_entities.py`（`TestCategoryIcon`）；`app/src/test/java/com/aozora/app/db/RepositoryIconSyncTest.kt`；先例：ADR-013（账户两列两端化）、T9-6 / REQ-13 决策 #7（照片 icon 的 wire 护栏）

> **为什么需要一份 ADR：** 本决策把两件**没有落点**的东西同时定死了——①分类图标此前在服务端**完全不存在**（无列、两个写入分支都不读、序列化也不出），两端化后它成为跨端数据；②跨端**只承载 ≤64 字符**，照片图标被**明确排除**在同步之外。后者是把一条既有的隐性代价（照片图标仅本机可见）**升格为显式契约**：后续每一张碰图标的卡（T19-2 的 AI 工具、任何图标 UI 改造）都会踩在它上面，而选错时**没有任何单元测试能从内部发现**——它只在「两台设备 / 换机 / bootstrap 重建」这些真实场景里暴露。

## 背景

### 六条影响结论的硬事实

| # | 事实 | 依据 |
|---|---|---|
| 1 | **服务端根本没有这一列**，且两个写入分支都不读、序列化也不出 | dev 库实测 `\d categories` = **10 列无 icon**（`books` / `accounts` 两表都有）；`sync/service.py` 的 `_apply_insert` category 分支不读 `payload["icon"]`、`_apply_update` 只处理 `name` / `parent_id`、`_serialize_entity` 的 Category 分支不出 icon 键 |
| 2 | **客户端有 icon，也一直在 push，服务端静默丢弃** | `CategoryEntity.icon: String?`；`categoryToJson` 当时**无条件** `put("icon", cat.icon ?: JSONObject.NULL)`，注释自述「服务端 Category 无此列，push 时未知键被忽略（安全）」 |
| 3 | **客户端靠一层本地保护才没被冲刷，但该保护是「无条件保留本地」** | 增量 pull 写的是 `localIcon ?: entity.icon`；而 `SyncModels.toCategory` **根本不读 icon** → `entity.icon` 恒为 null → 该表达式实为**恒保留本地**（服务端就算出 icon 也下不来） |
| 4 | **bootstrap 路径连读口都没有** | `serverChangeMapToCategory` 取 `map["icon"]`，但服务端不出该键 → 恒 null；而 bootstrap 前先 `clearAllEntities()` → **走一次快照重建，分类图标全丢** |
| 5 | **照片图标的 wire 护栏在 book/account 早已存在，category 恰恰没有** | `ICON_WIRE_MAX_LEN = 64` 的「超长省略键」护栏在 `bookToJson` / `accountToJson`（以及 savings/persona）都有；`categoryToJson` 是**唯一**无条件 put 的 |
| 6 | **服务端 64 上限是既有口径**（不是本轮新加） | `_apply_insert` / `_apply_update` 的 book、account 分支都用 `_validate_name(payload["icon"], 64, "icon")` |

### 问题的真正形态

用户要的是「对话改分类时也能改图标」。表面看是「给工具加一个参数」，**实际不是**：

- 只加参数 → AI 的改动**落不了库**（事实 1）。这正是 REQ-69 里 `account_type` 的原型：客户端一直在发、服务端一直不读，症状是「改了，同步一轮又变回去」。
- 两端化之后，**事实 5 立刻变成一颗毒丸**：服务端开始校验 `icon ≤ 64` 后，一个用**相册照片**当图标的分类（`data:` URI 可达 48KB）会让整批 push **422**——不是这一个分类失败，是**整批 mutation 一起失败**（第十八轮命名过的形态）。所以护栏必须与两端化**同批**落地，不能留到「以后再补」。
- 而护栏一落地，「照片图标不上云」就从**隐性行为**变成了**显式契约**：必须写下来，否则下一张卡会以为「图标都能同步」。

## 备选方案

| 方案 | 内容 | 评估 |
|---|---|---|
| **A. 放宽服务端上限，让照片图标也能上云** | `icon` 改 TEXT / 不校验长度 | **否决。** 48KB × N 个分类会灌进 change 表与每一份 bootstrap 快照（快照是整包下发的）；且照片图标「仅本机可见」是 T9-6 / REQ-13 决策 #7 已定的产品口径，本轮无诉求推翻它 |
| **B. 照片图标改走对象存储（先传图拿 URL 再存 icon）** | 复用 attachments 的存储链路 | **否决（本轮）。** 成本远超诉求——用户只要「对话能改图标」。真要支持，是另一张卡 + 一份新 ADR |
| **C. 两端化 + 跨端只承载 ≤64 字符**（**采纳**） | 服务端加列；客户端超长省略键（护栏）、服务端 64 校验兜底；照片保持仅本机 | 与 book/account 的既有口径**完全一致**（事实 5、6），无新概念；代价是照片图标换机即丢（既有代价，本轮只是把它写明） |
| **D. 只加 AI 工具参数，不加列** | 改 `category_update` 的参数了事 | **否决。** 改动落不了库（事实 1、3、4），复制 REQ-67 的症状 |

## 决策

### 决策 1：`categories.icon` 两端化，列为 `VARCHAR(64) NULL`，无默认值

- 服务端 `Category.icon`（迁移 `0023`，只加列、不给默认值、不加非空约束——照 `0022` 的规格）；客户端 `CategoryEntity.icon` **早已存在**，故 **Room 零版本跳跃**（不新增 schema 文件）。
- 存量行保持 NULL。**`null` 有语义**：客户端的 `CategoryIcon` 对 null 走「按名映射 emoji」（`CategoryEntity.icon` 的既有注释），所以服务端 NULL ≠「无图标」。

### 决策 2：跨端**只承载 ≤64 字符**（emoji / 短文本），照片图标保持「仅本机可见」

- 客户端 `categoryToJson`：`data:` URI（照片）等超长值**省略 icon 键**，防整批 push 422（毒丸）。服务端 `_validate_name(icon, 64)` 是**兜底**，不是主要防线。
- 服务端 64 上限**不放宽**（方案 A 已否决）。若要支持照片上云，须推翻本决策并另立 ADR（方案 B）。

### 决策 3：客户端 push 侧三分支——超长省略键、**null 显式上行**、其余原样

- `null` 必须**显式**上行（`put("icon", JSONObject.NULL)`），不能省略键：「清除自定义图标」是 `IconPicker` 里的真实操作（`onChange(null)`），而服务端 update 是「**键在场才改**」——省略键会让清除操作**静默失效**（旧值下次 pull 又回来）。
- 与服务端「键缺省不改」的口径配合，使**尚未升级的旧客户端**（不读 icon、不发该键）不会清掉服务端的图标。

### 决策 4：客户端 pull 侧合并**三态**（本决策最容易被抄错的地方）

| 本地 | 服务端 | 结果 | 理由 |
|---|---|---|---|
| `data:` 照片 | 任意 | **保留本地** | 照片仅本机可见（决策 2） |
| 任意 | **非 null** | **服务端权威** | AI 改图标 / 跨设备改图标**只走这条**——旧写法（无条件保留本地）会让服务端改了也永远下不来 |
| 任意 | **null** | **保留本地** | 服务端 NULL = 「无信息」：存量行全是 NULL，照单全收会在一次 pull 后抹掉本地已有的 emoji 图标。与 `accounts.account_type` **同口径**（ADR-013 决策 4） |

- 客户端 `SyncModels.toCategory` **必须**读 `icon`（本轮之前它不读——这正是旧保护「恒保留本地」的根因）。
- bootstrap 路径（`serverChangeMapToCategory`）复用同一读口，服务端出 icon 即自动恢复；它跑在 `clearAllEntities()` 之后，本地为空，**无需**合并逻辑。

### 决策 5：明确不做的事

不做照片图标上云、不做图标库 / 图标推荐、不改 `CategoryIcon` 的渲染与 emoji 宫格、不改分类名唯一约束 `uq_categories_scope_name`、不放开三级分类、不动 `parent_id` 语义与 `category.insert/update` 桥契约（键名同为 `icon`，无需映射）、不在服务端 `categories` REST 模块加 icon（那是 T19-2 的 AI 面）。

## 后果

### 残余与责任人

1. **照片图标不上云**（决策 2 的代价）：换机 / `bootstrap` 重建后，本地照片图标丢失（与服务端 NULL 无关——它从来没上去过）。这是 T9-6 / REQ-13 以来的**既有行为**，本轮只是把它写明。用户的感知路径＝「图标变回按名映射的 emoji」。
2. **「清空图标」不能跨设备传播**：服务端 NULL 同时来自「从未设置」与「A 设备清空」两种成因，而决策 4 把 NULL 一律当「无信息」（否则存量 NULL 会大面积抹掉本地 emoji——两害相权）。结果：A 设备清空后，B 设备 pull 到 NULL 仍保留自己的旧图标，直到 B 走一次 bootstrap。**这是 `null` 双语义的固有代价**，若要消除需引入显式哨兵值（本轮不做）。
3. **bootstrap 会丢「从未上行过」的本地 emoji 图标**：`clearAllEntities()` 先清库，服务端仍是 NULL 的行随清空消失；随后由 `migrateCloudEntitiesUp` 入队的 mutation 上行找回（同一轮内）。与 ADR-013 后果 1 形态相同。
4. **`categories` REST 模块（`server/src/modules/categories/schemas.py`）仍不带 icon**：本卡只做同步面；AI 读工具 `category_list` 出 icon 归 **T19-2**。