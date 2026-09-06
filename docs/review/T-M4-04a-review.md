# T-M4-04a 代码审查（权限状态机 + Bridge 扩展）

审查日期：2026-09-06
范围：PermissionManager.kt、ReminderScheduler.kt / ReminderReceiver.kt（附带修复）、AozoraBridge.kt、BridgeProtocol.kt、MainActivity.kt、AndroidManifest.xml、bridge-types.ts、native-bridge.ts、use-permissions.ts、相关测试。

## 门禁复核（审查时实测）

| 门禁 | 报告声称 | 实测 |
|---|---|---|
| npm run typecheck | ✅ | ✅ 通过 |
| npm test -- --run | ✅ 389/389 | ✅ 398/398（数量与报告不符，见 P3-3） |
| :app:compileDebugKotlin | ✅ | ❌ **失败** → 修复 C-1 后 ✅ |
| :app:testDebugUnitTest | ✅ 全部通过 | ❌ 被 C-1 阻塞 → 修复 C-1/C-2 后 ✅ 68/68 |

## 阻断问题（审查中已修复）

### C-1：AiGatewayClient.kt 缺 import，Kotlin 编译失败
- 现象：`Unresolved reference 'resumeWithException'` ×3（137/160/162 行）。
- 原因：只 import 了 `kotlin.coroutines.resume`，漏了 `kotlin.coroutines.resumeWithException`。
- 影响：`:app:compileDebugKotlin` 失败 → 全部 Kotlin 单测（含 PermissionManagerTest 8 个）无法运行。报告验证表中的两项 Kotlin ✅ 在交付时不成立。
- 归属：T-M4-02c 文件（非 04a 交付物），但 04a 报告声称的门禁被其阻断。
- 修复：补一行 import。修复后 68/68 测试通过。

### C-2：Kotlin BridgeProtocolTest 命令清单缺 T-M4-04a 的 6 个命令
- 现象：`test_allCommands_areDefined` 硬编码清单只到 AI 命令（断言 29），未含 permissions×3 / voice×2 / image×1；TS 侧同一守卫断言 35。
- 影响：该测试不反射枚举字段，缺项不会失败——Kotlin/TS 命令 parity 守卫对 04a 新增命令失效。报告将 BridgeProtocolTest.kt 列入交付物，但实际未同步。
- 修复：补 6 个命令、断言改 35。测试通过，两侧一致。

## 非阻断发现（未修，建议）

### P2-1：requestPermission 检查顺序
`pendingCallback != null → DENIED` 排在 `GRANTED 短路` 之前。当另一权限请求进行中时，请求一个已授予的权限会错误返回 DENIED。建议把 GRANTED 检查提到 pending 检查之前。

### P2-2：「已请求」标记写入时机偏晚
prefs 标记只在 launcher 回调里写。若权限对话框期间 Activity 重建（旋转/深色模式切换），pendingCallback 丢失、系统结果投递到新 launcher 实例但 callback 为 null → 用户已拒绝而状态机停留在 NOT_REQUESTED。建议在 `launcher.launch()` 调用时即写标记。

### P2-3：POST_NOTIFICATIONS 无应用内请求路径（跨任务缺口）
ReminderReceiver 在 Android 13+ 无该权限时静默跳过通知，但 PermissionManager 白名单（keyToPermission）与前端 PermissionType 均不含 POST_NOTIFICATIONS，`reminder.checkPermission` 只读不请求 → Android 13+ 上每日提醒永远不可见且无引导。属 T-M4-05a 范畴，建议下一任务处理（加入白名单或 reminder.set 时触发请求）。

### P3-1：PERMANENTLY_DENIED 启发式误报
`granted=false && !shouldShowRequestPermissionRationale` 会把「用户未做选择直接关闭对话框」也判为永久拒绝。常见启发式局限，可接受；UI 文案应保留重试入口，不要只给「去设置」。

### P3-2：重言断言
ReminderSchedulerTest.hasNotificationPermission_api33 的 `assertTrue(result || !result)` 恒真，无验证价值。Robolectric SDK33 默认未授予，应断言 `assertFalse(result)`。

### P3-3：完成报告数字不准
- 「命令数量 25→31」：实际 TS 测试从 22 → 35（其中 04a 贡献 6 个，reminder/AI 的 7 个属之前任务的补录）。
- 「389/389」：当前实测 398/398。
- BridgeProtocolTest.kt 列入交付物但未含 04a 命令（见 C-2）。

## 确认无问题的设计点

- **注入面**：permissions.request 经 keyToPermission 白名单限定 3 种权限，WebView 无法请求任意权限；结果推送用 `JSONObject.quote` 转义。✅
- **时序**：registerLaunchers() 在 setContentView/STARTED 之前调用，符合 registerForActivityResult 约束。✅
- **提醒链**：ReminderReceiver 先链式调度次日再检查通知权限，权限被拒不中断调度链。✅
- **精确闹钟降级**：Android 12+ 无 SCHEDULE_EXACT_ALARM 时降级 setWindow，避免 SecurityException。✅
- **版本分支**：READ_EXTERNAL_STORAGE maxSdk=32 与 imagePermission() 的 TIRAMISU 分支一致；permissionToKey 将两者统一映射到 READ_MEDIA_IMAGES key。✅
- **TS 订阅链**：onPermissionResult 多订阅者 prev 链式包装 + unsubscribe 条件还原，正确。✅
- **附带修复方向**：ReminderScheduler object→class、WorkManager→AlarmManager，与 AozoraApp / BootReceiver / AozoraBridge / 测试的调用方式一致，修复合理。✅
- **Manifest**：receiver exported 属性、权限声明齐全。✅

## 审查中改动的文件

1. `app/src/main/java/com/aozora/app/ai/AiGatewayClient.kt` — +1 行 import（C-1）
2. `app/src/test/java/com/aozora/app/bridge/BridgeProtocolTest.kt` — +6 命令、断言 29→35（C-2）

## 结论

T-M4-04a 核心实现（权限状态机、Bridge 扩展、hook、附带修复）质量良好，无设计缺陷；但交付时验证表不成立（C-1 阻断了全部 Kotlin 门禁），且 Kotlin parity 测试未同步（C-2）。两处已在审查中修复并复跑全部门禁通过。P2-1/P2-2/P2-3 建议随 T-M4-04b/05a 处理。
