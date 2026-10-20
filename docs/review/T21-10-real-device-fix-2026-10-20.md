# T21-10 · 自动记账真机回归与修复（2026-10-20）

**性质：** 第二十一轮 REQ-83 收口后的**真机增量回归 + 缺陷修复**（Bug-fix 模式）。
**设备：** Xiaomi 2410DPN6CC / Android 16（API 36）/ HyperOS 3.0，`adb` 序列号 `26231ebf`。
**APK：** 0.1.0 debug（`http://116.62.196.255/aozora-api/api/v1`），含 T21-9 两处修复。
**测试账号：** 已登录真机账号（152****0806），自动记账开关已开、通知使用权已授。

> T21-9 收口的决策 #19 七项是在**模拟器 Pixel_7_API_36**上取证的；本卡是在**真实支付 App
> （支付宝/微信）+ 真实账户**上的首次验证。两者环境差异直接暴露了模拟器无法覆盖的缺陷。

---

## 一、决策 #19 七项在真机上的实测

| # | 项 | 操作与样本 | 结果 |
|---|---|---|---|
| ① | 两授权分别生效 | 通知：`cmd notification post` 投递支付通知（shell 来源）；无障碍：真实支付宝支付 | **部分通过**：通知通道正常；无障碍在服务连接 `DEAD` 期间**零采集**（见问题 3） |
| ② | 非支付内容零落库 ×2 | 通知侧：验证码 / 聊天 / 广告 / 系统更新各一条；无障碍侧：支付宝首页、账单列表页 | **通知侧通过**（4 条零落行）；**无障碍侧失败**（首页落 5 行、账单列表落 2 行，见 R-PROD-65） |
| ③ | L1 去重 | 同 tag 同文本连续投递 2 次 | **通过**：只新增 1 行（¥1.11） |
| ④ | L2 合并且保留商户名 | 真实支付宝 ¥1.00 支付：通知通道 + 无障碍通道各抓一条 | **通过**：通知行 `mergedInto` 指向无障碍行（`008fdec1`），保留商户 `*松` |
| ⑤ | L3 不合并 | 同额 ¥6.60、商户不同（星巴克 / 麦当劳） | **通过**：两行各自 committed、`mergedInto` 为空 |
| ⑥ | 断网捕获 + 联网补 | — | **本卡未执行**（见 §五 挂账） |
| ⑦ | 撤销任一授权后对应通道停用 | — | **本卡未执行**（见 §五 挂账） |

### 无障碍真实页面取证（正向）

支付宝 ¥0.01 收钱码支付的**账单详情页**节点树完整可读：

```
*松 / 支出0.01元 / 交易成功 / 支付时间 2026-10-20 15:23:10
付款方式 余额宝 / 商品说明 收钱码收款 / 收款方全称 *松(个人)
```

落行结果：`accessibility` / `committed` / conf 0.9 / 金额 1 分 / 商户 `*松` / 分类「交通」——
金额、商户、方向、分类四项全对。

**决策 #18 ②（FLAG_SECURE 支付保护页）取证结论：** 支付宝支付成功页与账单详情页
（`XRiverActivity`，H5 容器）**节点树可读**，没有 FLAG_SECURE 阻断。这是首次在真实
第三方支付页面上取到的正面证据（T21-9 只有 Chrome 托管页）。残余：微信支付页、银行 App
仍待取样。

### 通知通道真实样本（R-PROD-59 补证）

真实支付宝支付通知（`com.eg.android.AlipayGphone`）被正常捕获：

```
交易提醒 你有一笔1.00元的支出，点击领取8个支付宝积分。
交易提醒 你有一笔0.01元的支出，点击领取8个支付宝积分。
```

两条都是 `pending_confirm`（conf 0.8、无商户名）——符合 PR-2「低置信不猜字段」。
**注意**：Android 15+ 的「不可信 listener」正文抹除在本机**未发生**（正文完整）。

---

## 二、发现的问题

### R-PROD-64（P0）· 同一支付页渐进渲染导致重复入账

**现象：** 真实支付宝 ¥1.00 支付产生 **2 条 committed 的 `auto_captures` 行**、**2 条交易**：

```
row18 accessibility committed tx=dc6c2b94  「支付成功 ¥ 1.00 收款方 *松 交易方式 建设银行储蓄卡(7469) 完成」
row19 accessibility committed tx=9f157427  「支付成功 ¥ 1.00 … 与车主**师傅同行感受如何? … 领取支付宝积分 … 完成」
```

两行相隔 **223ms**，是同一支付页的两次渲染帧。

**根因：**
1. `DedupeKeys.l1Key` 的输入含**归一化文本**——文本一变，L1 键就变，唯一索引拦不住；
2. `AutoBookAccessibilityService` 收到 `TYPE_WINDOW_CONTENT_CHANGED` **立即落行**，
   内存签名只挡"文本完全相同的重复"；
3. 服务注释原写「L1 的分钟桶 + 归一化文本仍能把它合并为同一行」——**与实现不符**
   （分钟桶只解决时间维度，文本维度没有归一）。

**影响：** 用户被重复记账（同一笔支付记两次），金额统计失真。

**修复：** 页面稳定窗口——内容变化后静置 `STABLE_WINDOW_MS = 1500ms` 才落行，
期间再有变化就顶掉旧快照；**页面切换时立即定型**（`flushPending()`），避免
"支付完成立即跳回商户 App"丢掉最终形态。事件时刻在**收到变化时**捕获，落库被推迟
但时间戳不跟着推迟。

### R-PROD-65（P1）· 汇总页（账单列表 / 首页）误落库

**现象：** 无障碍通道在支付宝**首页**落 5 行、**账单列表页**落 2 行（均 `ignored`，
未入账，但 `rawText` 已写入本地库）。列表页 rawText 带出整页历史账单，含
**卡号后四位 `6207880210033305`、手机号 `15205690806` / `13260969365`**、商户与金额。

**根因：** `AccessibilityFilter` 判据为「事件词 + 带货币标记的金额」，汇总页同时满足：
- 首页：`付款成功￥0.01` / `付款成功￥16.51` 两条最近交易 + 导航项；
- 账单列表页：`¥576.53` 月度汇总 + 十几行 `-xx.xx元`。

**影响：** 违反决策 #13「非支付页面连文本都不落库」的隐私承诺；本地库留存整页
账单与个人标识。虽被本地规则判 `ignored` 未入账，但落库本身即越界。

**修复：** 新增汇总页守卫——**页面出现 ≥2 个不同金额值即拒绝**（`a11y_multi_amount`）。
单笔交易页只显示本笔金额；同一金额重复出现（`¥32.00` 多处显示）去重后仍是 1 个，不受影响。

### R-PROD-66（P0）· 待确认箱「确认」报 `no default book`（原始英文错误直接展示）

**现象：** 待确认箱顶部显示红色 `no default book`；点「确认」无法入账。

**根因：** `AozoraBridge.buildTransactionFromCapture` 取记账账本时**只读 `default_book_id`**：

```kotlin
val bookId = repository.getDefaultBookId() ?: throw IllegalArgumentException("no default book")
```

而 `default_book_id` 只有用户**显式设置过默认账本**才存在。聊天/手动记账走的是
`Repository.getEntryBook()` 的**回退链**（default_book_id → 第一个非总账本活跃账本），
自动记账确认路径漏了这个回退 → 真机上（从未设过默认账本）直接抛异常。

**影响：** 自动记账的确认/改后确认**完全不可用**；且前端把原始英文异常直接渲染给用户。

**修复：** 改成 `repository.getEntryBook()?.id ?: throw ...`，与手动记账同口径。
**守护：** `AozoraBridgeAutoBookTest.resolve_confirm_fallsBackToEntryBookWhenDefaultBookIdMissing`
（删掉 `default_book_id` 键后确认仍能入账到活跃账本），已做反向验证（改回旧实现即变红）。

### R-PROD-67（P1）· 多金额守卫误杀「带营销金额的支付成功页」

**现象：** 用户支付 ¥0.04 后，通知通道抓到（`pending_confirm`），**无障碍通道零落行**。

**根因：** R-PROD-65 新增的汇总页守卫以「页面出现 ≥2 个不同金额」为判据。支付宝
收钱码支付成功页带有营销元素（如「最高 18.8 元红包瓜分机会」「+8 积分」等），页面里
出现两个及以上带货币标记的金额 → 被误判为汇总页。

**影响：** 部分支付成功页的无障碍采集被吞；通知通道兜底（进待确认箱），不丢账但
少一个通道、少商户名。

**状态：** **未修（本卡登记）**。修法需要区分"营销金额"与"交易金额"（如按金额
出现位置/字号/是否紧跟「最高/奖励/积分」），属于过滤器规则细化，需单开卡并补
真实页面样本。**残余**：R-PROD-65 的已知误杀面在此具体化为真实案例。

### R-PROD-68（P2）· 待确认卡片与手动记账卡片视觉不一致

**现象：** 待确认卡是独立布局（铃铛来源图标、36px 图标格、15px 标题、`-¥0.04`
带 ¥ 符号），与明细页流水行（`TxListRow`：40px 分类图标格、19px 图标、15px 标题、
金额无 ¥）明显不同。

**修复（本卡）：** 待确认卡头部改为复用 `TxListRow`（与明细页/首页同一组件），
只保留下方「确认/修改/忽略」按钮行与徽标行；`TxListRow` 增加可选 `amountLabel`
（解析缺金额时显示「—」）。用例 15/15 保持通过。

---

## 三、其他现场观察（未达缺陷门槛，记录备查）

1. **HyperOS 3.0 无障碍授权多一层「危险」弹窗**：必须勾选「我已知晓可能存在的风险」
   并等 **8 秒倒计时**结束才能点红色「确定」。不完成该确认，开关永远不生效（无任何报错）。
   → R-PROD-62 的真机形态补充：授权引导文案应提示「若弹出风险确认页，需勾选并等待」。
2. **无障碍服务连接反复 `DEAD`**：`dumpsys activity processes` 同时存在 3 条
   `AutoBookAccessibilityService` 连接记录（2 条 DEAD、1 条活）。首笔支付恰逢
   DEAD 窗口 → 全程零采集；返回+重进页面触发 STATE_CHANGED 后恢复。
   → 疑似 HyperOS 省电/重绑策略，**与 R-PROD-62 同族**，列为挂账（见 §五）。
3. **Aozora 自身 WebView 在 uiautomator 节点树中为空**（`NAF=true`）——与本次修复无关
   （自家包被排除），但提示：第三方 App 若同样不暴露 WebView 节点，无障碍将读不到。
4. **支付宝 H5 页面（Nebula `XRiverActivity`）节点树完整可读**，含原生 TextView 与
   WebView 混合结构。

---

## 四、修复内容与回归守护

### 改动文件

| 文件 | 改动 |
|---|---|
| `app/src/main/java/com/aozora/app/autobook/AutoBookAccessibilityService.kt` | 页面稳定窗口（`PendingSnapshot` + `schedulePersist()` + `flushPending()` + `MAX_WAIT_MS` 硬上限）；`currentPageKey` 缺失时用当前事件兜底；`composeText(pkg)` 校验活动窗口属于事件来源包；落行逻辑抽为 `persist()` |
| `app/src/main/java/com/aozora/app/autobook/AccessibilityFilter.kt` | 新增汇总页守卫（`REASON_MULTI_AMOUNT`） |
| `app/src/main/java/com/aozora/app/autobook/NotificationFilter.kt` | 新增 `extractAllAmountCents()`（复用同一套金额数学） |
| `app/src/main/java/com/aozora/app/bridge/AozoraBridge.kt` | 待确认确认路径改用 `getEntryBook()` 回退链（R-PROD-66） |
| `app/src/components/TxListRow.tsx` | 新增可选 `amountLabel`（解析缺金额时显示「—」） |
| `app/src/features/autobook/AutoBookCapturePage.tsx` | 卡片头部复用 `TxListRow`（R-PROD-68） |
| `app/src/test/java/com/aozora/app/autobook/AccessibilityFilterTest.kt` | +3 条（账单列表页 / 首页两条反例 + 同额重复正例） |
| `app/src/test/java/com/aozora/app/autobook/NotificationFilterTest.kt` | +1 条（`extractAllAmountCents` 去重与货币标记） |
| `app/src/test/java/com/aozora/app/security/SecurityConfigTest.kt` | +1 条结构守护（稳定窗口常量 / delay / flushPending 在场） |

### 反向验证（`AI协作稳定性守则.md` §3.2）

- 把多金额判据临时改为 `>= 99`（等价关闭）→ `billListPage_*` / `homePage_*` 两条**变红**；
- 恢复 `>= 2` → 全绿。
- 稳定窗口为**结构守护**（常量与调度点在场），行为验证由真机复测承担——时序逻辑
  依赖 Android 框架与真实节点树，JVM 侧无法构造（同 T21-4 口径）。

### 残余风险

1. 稳定窗口只折叠 **1.5s 内**的渲染帧。若页面在稳定后再次变化（如相对时间
   "8分钟前"→"9分钟前"），同一页仍可能产生第二行；真正兜底需要解析层同笔幂等
   （本卡未做，见挂账）。
2. 多金额守卫的边界：同一页面若同时显示"原价 ¥50 / 实付 ¥32"，会被判为汇总页拒绝。
   当前真实支付宝/微信支付结果页不这样显示；若后续页面形态变化需重新评估。

---

## 五、挂账

| # | 项 | 原因 |
|---|---|---|
| 1 | 决策 #19 ⑥ 断网捕获 + 联网补 | 本卡聚焦 P0/P1 修复，断网回归待修复版 APK 装上后补 |
| 2 | 决策 #19 ⑦ 撤销任一授权后停用 | 同上；且撤销/恢复无障碍会触发系统确认流程，耗时较长 |
| 3 | 无障碍服务连接反复 `DEAD` | HyperOS 系统层行为，需专项观察；与 R-PROD-62 同族 |
| 4 | 解析层同笔幂等（稳定窗口之外的第二道防线） | 改动面涉及 AutoBookEngine，本卡按用户选定的"稳定窗口"方案执行 |

---

## 六、真机复测（修复版 APK）

**复测环境：** 同一台真机；每次 `pm install -r` 后无障碍绑定失效（R-PROD-62），
均走系统设置页重新授权（含 HyperOS「危险」确认：勾选 + 8 秒倒计时）。

| 项 | 操作 | 结果 |
|---|---|---|
| P1 汇总页零落行 | 打开支付宝账单列表页 + 首页各停留 5 秒 | **通过**：`auto_captures` 保持 20 行，零新增（修复前 7 行） |
| 单笔详情页落行 | 打开 ¥2.00 账单详情页（`支出2元 / 交易成功 / 收款方全称 *松(个人)`） | **通过**：落 1 行 `accessibility`、`committed`、商户 `*松(个人)`、金额 200 分，生成 1 条交易 |
| 稳定窗口（P0） | 支付页渐进渲染场景 | **部分验证**：本轮详情页落行只产生 1 行（修复前同场景会因文本变化产生多行）；**完整支付成功页的复测因设备掉线待补** |
| 诊断取证 | 用应用私有目录文件替代日志（HyperOS 过滤了本应用的 logcat） | 证据显示：事件正常到达（type=2048）、`persist` 判定链路正常、跨包错配被拦截（`mismatch root=com.android.settings/com.miui.home`） |

### 复测中确认的系统层行为（并入 R-PROD-62）

1. **`am force-stop` 会撤销无障碍授权**（`enabled_accessibility_services` 被清空）——
   用户"用着用着失效"的一种来源。
2. **重启后锁屏状态下服务无法绑定**：FBE 使 `/data/user/0/<pkg>` 不可访问，
   `run-as` 报 "No such file or directory"；解锁后自动恢复。
3. **应用进程被 HyperOS 判为 cached（`oom_score_adj=200`）时事件不投递**，
   即使 `dumpsys accessibility` 仍显示 bound。修复动作：重启设备（已验证有效）。
4. **HyperOS 过滤本应用的 logcat 输出**（连 chromium 的 E 级日志都不可见）——
   真机排障不能依赖 `Log.*`，本卡改用应用私有目录诊断文件取证（交付前已移除）。

### 待补验证（设备掉线中断）

- 修复版上**再次真实支付一笔**，确认同一笔只落 1 行、只入账 1 笔（P0 完整闭环）。
- 待确认箱点「确认」能正常入账（R-PROD-66 修复）与卡片新样式（R-PROD-68）。
