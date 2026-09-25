# ADR-015 移除离线语音输入（Vosk），改由系统输入法承担

**状态：** 已采纳（2026-09-25）
**范围：** 第十九轮 REQ-75（T19-7 客户端 / T19-8 服务端），属 **L2 变更**（移除一项端上能力、一条设备能力与一项权限；**协议本身不变**）
**关联文档：** `server/src/modules/ai/devices.py`；`server/src/modules/ai/help.py`；`app/build.gradle.kts`；`app/src/main/AndroidManifest.xml`；`app/src/main/java/com/aozora/app/bridge/BridgeProtocol.kt`；`docs/adr/ADR-010-device-capability-channel.md`（**追加注记，不回改正文**）；`docs/architecture/ai-architecture.md`（设备能力目录一节）；`docs/APP-CONTEXT.md`（T-M4-04 范围变更 / D-PROD-16）；`docs/legal/privacy-policy-v1.md`（权限管理一节）

> **为什么需要一份 ADR：** 这不是一次「删掉一个功能」的常规清理，而是**同时收回四样东西**——①一条**设备能力**（`voice.transcribe`，两侧同时下架，见决策 2）；②一项**系统权限**（`RECORD_AUDIO`，两端都不再声明）；③一份**帮助语料**（`HelpTopic(id="voice")`，它会被 `help_search` 直接答给用户）；④**约 131 MB 的包体**。其中①是最容易做漏的一侧：只删客户端会让模型**继续调一条无人应答的能力**（每次白等一个超时窗，R-PROD-35 描述过的形态），而只删服务端则留下一个点了没反应的 🎤 入口。两侧必须**同轮**收，且「先服务端、后客户端」才是安全顺序。

## 背景

### 诉求与理由

用户原话：「我发现现在输入法自带语音转文字, 这让我发现我装在客户端的 vosk 离线语音识别转文字服务有些没用, 而且让客户端的安装包很大, 现在我打算去掉这个语音服务, **你梳理一下和这个语音服务有关的全链路代码逻辑, 然后进行干净的逻辑清洗**」。

两条理由都是可验证的：**替代品已存在**（系统输入法的语音转文字，用户随时可用，且不占用 App 的包体与内存）与**包体代价**（见下）。因此这是一个**产品能力取舍**，不是缺陷修复。

### 七条影响结论的硬事实

| # | 事实 | 依据 |
|---|---|---|
| 1 | 依赖 `com.alphacephei:vosk-android:0.3.47` 是 `implementation`（非 compileOnly），随包分发 | `app/build.gradle.kts` |
| 2 | 模型 assets 实测 **131 MB**（下载脚本自注 zip ~42 MB），且 `build.gradle.kts` 未配 `abiFilters` / `packaging` / `jniLibs` → AAR 自带的 `libvosk.so` + `libjnidispatch.so` **× 3 ABI 全进包** | `app/src/main/assets/vosk-model-small-cn/`；`app/download-vosk-model.sh` |
| 3 | 端上服务 `VoiceRecognitionService` 有完整生命周期接线（单例、预加载、释放） | `AozoraApp.kt` / `MainActivity.kt` |
| 4 | **桥命令 2 条**：`voice.startRecording` / `voice.stopRecording`（权威计数 85 → 83；另有子集口径 47 → 45） | `BridgeProtocol.kt`；`SecurityConfigTest.kt` |
| 5 | **它同时是一条设备能力**：客户端 `device-executor.ts` 的 `voice.transcribe` 分派 + 服务端 `devices.py` 的 `DeviceTool(name="device_transcribe_voice")` | 两侧各一处 |
| 6 | **权限族**：`RECORD_AUDIO` 出现在 manifest、`PermissionManager`、桥的两张映射表与 `canRecordAudio` 状态、TS 契约，以及服务端 `device_request_permission` 的权限枚举 | 两端多处 |
| 7 | **帮助语料**：`HelpTopic(id="voice")` 会被 `help_search` 答给用户（「点 🎤 说一句……识别用的是手机上的离线模型」） | `server/src/modules/ai/help.py` |

### 问题的真正形态

**语音在这套架构里跨了三层，而三层各有自己的「谁来执行」：**

- **端上执行体**（`VoiceRecognitionService`）：只有客户端有。
- **桥命令**（`voice.startRecording` / `stopRecording`）：前端与 Kotlin 之间的契约。
- **设备能力**（`voice.transcribe`）：**服务端 loop 也能触达它**——模型可以主动决定「让用户说一句」。这一层最容易被漏掉：它在服务端只是一行 `DeviceTool` 声明，删掉客户端不会有任何编译错误，只会让模型调到一个**没人应答**的能力上。

因此这条链的删除**没有一个自然的失败点会提醒你漏了一侧**——唯一能暴露它的是「模型真的去调它」这个真实场景。这正是本 ADR 要把它写死的原因。

## 备选方案

| 方案 | 做法 | 结论 |
|---|---|---|
| **A. 开关式半移除** | 保留代码与 assets，加一个开关隐藏入口 | **否决。** 用户给的两条理由（包体、已被输入法替代）它一条都不解决：包体不减，维护面不减，只多一个永远不会被打开的开关 |
| **B. 只删客户端** | 移除端上服务与入口，服务端 `device_transcribe_voice` 照旧 | **否决。** 模型仍能调这条能力，每次调用**白等一个超时窗**才收敛（R-PROD-35 的形态）；用户看到的是「卡住两分钟然后说没做成」 |
| **C. 只删服务端** | 下架 `device_transcribe_voice`，前端 🎤 入口照旧 | **否决。** 入口还在但下游能力没了，用户点了没反应——比不做更糟 |
| **D. 两侧同批完整移除** | 依赖 + assets + 脚本 + 端上服务 + 2 条桥命令 + TS 契约 + 前端入口 + 权限族 + 设备能力 + 帮助语料 | **采纳** |

**顺序**：先服务端（下架能力，模型不再看得到它）、后客户端（拆端上链路）。反过来做会留一个「模型能调、客户端不认」的窗口。

## 决策

### 决策 1：**完全移除**，不留开关、不留隐藏入口

包括依赖、模型 assets、下载脚本、端上服务及其生命周期接线、2 条桥命令、TS 契约与桥方法、前端 hook / UI / 文案 / placeholder、`RECORD_AUDIO` 权限族、设备能力（两侧）、帮助语料，以及现行文档里的口径。

### 决策 2：设备能力**两侧同批下架**

服务端下架 `DeviceTool(name="device_transcribe_voice", capability="voice.transcribe")`；客户端下架 `device-executor.ts` 的 `VOICE_CAPABILITY = 'voice.transcribe'` 分派与 `use-assistant` 的 device-voice 三方法。**判据是「模型的工具清单里不再有它」**——下架后模型不可能再产出对它的调用，因此不存在可达的超时窗。

### 决策 3：`RECORD_AUDIO` **两端一起收**

客户端不再在 manifest 声明麦克风权限、不再有 `canRecordAudio` 状态与映射表项；服务端 `device_request_permission` 的权限枚举同批删掉 `RECORD_AUDIO`（连描述里的「录音前申请」一并改掉）。理由与决策 2 同形：**App 不再声明这项权限，留着枚举值只会让模型调到一个必然失败的值**（客户端对未知权限名回 `invalid_input`）。**保留**整套权限机制与其余三项权限（`CAMERA` / `READ_MEDIA_IMAGES` / `POST_NOTIFICATIONS`），`device.execute` / `device.result` 通道本身与图片链路一概不动。

### 决策 4：帮助语料**下架**，但**不新增**「我们不支持语音」条目

删 `HelpTopic(id="voice")`，并把 `chat` 条目里「输入框旁边还能用语音和图片」改为只提图片。**不新增**一条「语音输入不支持」的帮助：`help.py` 的语料口径是「**不含未实现的功能**——要么不提，要么如实写」，而 `entry` 字段要求「没有入口的功能不写进语料」，这里已经没有任何入口可写。语音问法的检索结果因此是**空**——这是**预期行为**：`help_search` 的未命中路径会把条目清单一并回给模型并叮嘱「条目里没有的就如实说不知道」，模型不会照着一条不存在的功能向用户承诺。

### 决策 5：明确不做的事

不引入任何**新的**语音方案（系统输入法自带即既定替代）、不保留隐藏入口、不动相机 / 照片 / 图片识别链路、不改 `device.execute` / `device.result` 协议本身、不动本地规则 NLU（那是另一条离线能力，REQ-71 已定型）、**不回改** `ADR-010` 正文与 `docs/review/` 下的历史评审记录。

## 后果

### 残余与责任人

1. **离线状态下没有语音输入**（决策 1 的代价，也是本决策被接受的前提）：断网时用户仍可用手动表单与本地规则 NLU 记账，只是不能语音转写。产品判断是「系统输入法随时可用」，因此不视为能力缺口。
2. **包体回收需量化**：assets −131 MB、native libs −3 ABI × 2 库、AAR 依赖本身。前后对照（APK / AAB 体积）归 **T19-7** 实测、收口卡登记。
3. **桥命令计数与子集口径**：权威 **85 → 83**、子集 **47 → 45**；四处同步（`BridgeProtocol.kt` 常量与 `ALL`、`SecurityConfigTest` 断言、TS `bridge-types.ts`、TS 镜像列表）归 **T19-7**。
4. **`ADR-010` 的两处样例失效**（决策 6 的「一对多」样例、已知残余第 4 行的「6 个能力」）：已在该文末尾**追加注记**说明，正文保持原样。
5. **风险册措辞**：R-PROD-35 的「六能力」需改为**五能力**、R-PROD-19 的「语音打断」措辞需订正——**统一归 T19-9 收口落笔**（中间卡只在交付记录里描述）。
6. **历史文档保留原文**：`docs/testing/`、`docs/milestones/`、`docs/engineering/T-M4-04b-*` 等记录当时的实现与验证结论，**不改写**，只在文首加一条「该能力已移除」的注记。
7. **帮助检索的真实答法未在 jsdom / 单测下验证**：单测只能钉住「检索结果为空」，模型最终怎么措辞（「帮助里没有」还是「不支持」）需真机对话验证，归 **T19-9** 的真机项。