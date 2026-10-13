# Google Play 申报口径（无障碍 API）

**状态：** 已定稿（2026-10-13，第二十一轮 REQ-83 / T21-1）
**适用范围：** Google Play 上架的**无障碍服务声明**、商店列表披露与数据安全表单
**配套物料：** `docs/legal/accessibility-disclosure.md`；`docs/legal/accessibility-store-declaration-cn.md`；`docs/adr/ADR-016-autobook-capture-architecture.md`

> **适用性说明：** Aozora 首发面向国内商店，Google Play 属后续渠道（`docs/product/non-goals.md` 未把 Play 列为非目标，iOS 才是）。本文件先行定稿，是因为**无障碍 API 是 Play 政策的高敏项**，一旦上架必须提前满足；且 `isAccessibilityTool` 是**配置层声明**，改它要动 `accessibility_service_config.xml`，属于 T21-4 的实施范围——**口径必须先定死**。

## 一、配置层声明：`isAccessibilityTool="false"`

`res/xml/accessibility_service_config.xml` 中声明：

```xml
android:isAccessibilityTool="false"
```

**理由（必须能对审核讲清楚）：** 该属性表示「这个服务是为**帮助残障用户**使用设备而提供的」。Aozora 的自动记账服务**不是**为残障辅助而设计——它是给普通用户省去手动记账的便利功能。按 Google 的定义，**非辅助用途的服务必须声明为 `false`**，冒充辅助工具上架是政策违规。

**声明 `false` 的代价**：Android 会在系统无障碍设置里对本服务显示更醒目的提醒（提示用户该服务并非无障碍辅助工具），且 Android 15+ 对 `false` 的服务有更严的启用限制。**这是诚实代价，必须接受**——不得为了「少一句系统提醒」而谎报 `true`。

## 二、Play 政策要求的三件事

| 要求 | 本产品的兑现 |
|---|---|
| **在 Play Console 声明用途** | 在「无障碍服务」专项声明中填写：用途 = 读取支付页文本以自动记账；读取范围 = 仅用户选定的支付类应用在支付相关页面时；不执行操作 = 只读不写 |
| **显著披露（Prominent Disclosure）** | 授权引导页在跳转系统设置**之前**，用**独立、醒目**的说明（非埋在长文里）讲清读什么、不读什么、不操作什么；文案基线见 `accessibility-disclosure.md` |
| **获得用户同意** | 能力**默认关闭**，须用户显式授权 + 主动开启；系统设置页的授权即构成这一同意 |

## 三、数据安全表单（Data Safety）的对应口径

| 表单问题 | 本产品的回答依据 |
|---|---|
| 是否收集其他应用的信息？ | 仅在你显式授权后，读取选定支付类应用的**支付相关页面文本**用于自动记账；非支付内容当场丢弃 |
| 数据是否加密传输？ | 上行仅 HTTPS/TLS（与 AI 助手同一网关） |
| 用户能否要求删除数据？ | 账号删除走既有流程；采集原文存本地库，随账号删除 / 应用卸载清除 |
| 数据是否用于广告或第三方共享？ | 否（`privacy-policy-v1.md` §5：不售卖、仅 AI 功能涉及第三方模型调用） |

## 四、提交前逐条核对清单

- [ ] `accessibility_service_config.xml` 已声明 `android:isAccessibilityTool="false"`
- [ ] 配置**不**声明 `canPerformGestures`（与服务只读口径一致）
- [ ] Play Console「无障碍服务」专项声明已填写，措辞与 `accessibility-disclosure.md` 一致
- [ ] 应用内授权引导页含**显著披露**（跳系统设置前的独立说明），不含「100% 自动」类表述
- [ ] 数据安全表单按第三节口径填写
- [ ] 若上架时 Google 政策已更新，**以当次政策为准**逐条复核本文件