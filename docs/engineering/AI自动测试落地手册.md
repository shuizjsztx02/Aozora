# AI 自动测试落地手册（模拟器 + 安卓真机双端）

> **面向**：测试同事、新加入的 QA、想在本项目接手的协作者。
> **来源**：Aozora 项目第十七～二十轮（T17–T20）的真实执行记录。每一条结论背后都有一次实际踩坑，不是推演。
> **配套读物**：`docs/testing/T-M7-ai-tool-matrix-2026-09-21.md`（首个完整案例）、`docs/engineering/long-running-project-playbook.md`（协作规范）。

---

## 〇、先讲清楚一件事：这套方法在测什么

### 0.1 它不是什么

它不是「用 AI 代替人点按钮」。**让 AI 去点按钮，反而比 Selenium/Appium 更贵、更不稳定**——模型每次点哪里是随机的，脚本不可复现，失败无法归因。

### 0.2 它是什么

它把「AI 自动测试」拆成**三层测量面**，每层各测一件事，且**判据是数据不是脚本**：

| 测量面 | 怎么打 | 量的是什么 | 成本 |
|---|---|---|---|
| **离线面** | `run.py --dry-run` + vitest | 确定性的东西：路由词表覆盖、客户端闸门、链式依赖 | 秒级，0 模型调用 |
| **服务端面** | `run.py` 直打 `POST /ai/agent` | **模型层**：选对工具没有（准确性）、写操作落库没有（有效性） | 全量约 25 分钟 |
| **设备面** | `device_flow.mjs` 经 CDP 驱动真 UI | **可达性**：这句输入在设备上到底能不能走到 AI；确认卡渲染；真落库 | 每条 30–60 秒 |

**为什么必须分成三层**——同一句话在三个面上量到的可能是三件事：

> 实例：`TX-ADD-04`「刚收到工资 12000 到卡里」
> - 服务端面：**失败**。单字信号「卡」把整句路由进账户域，`transaction_add` 根本不在注入面 → 模型只能反问「记到哪个账户？」（真缺陷）
> - 设备面：**成功**。因为客户端本地快路径先把句子接走了，用户看到的是正确结果。
> - 结论：**只看服务端会高估危害，只看设备会漏掉缺陷**。必须是三层。

### 0.3 一句话概括方法论

> **把测试需求写成数据（语料库），让三种执行器去打同一个语料库，判据与执行分离。**

所以后续**只更新测试需求 = 只改 `corpus.json`**，不用碰任何脚本。

---

## 一、六条第一性原理

### 1.1 语料库驱动：测试需求是数据，不是代码

一条用例长这样（`server/scripts/ai_tool_matrix/corpus.json`）：

```json
{ "id": "TX-ADD-01", "layer": "core", "text": "记一笔 12.5 元的早餐",
  "expect": ["transaction_add"], "landing": "produced" }
```

- **加测试需求 = 往数组里加一条 JSON**。不需要写 Python、不需要写 JS、不需要懂框架。
- 服务端执行器、设备执行器、离线闸门用例**共用同一份语料**——三个面量的永远同一批需求。
- 语料分五层，用途不同：

| 层 | 条数 | 写什么 |
|---|---|---|
| `core` | 59 | 每个工具的主干说法，直白式 |
| `para` | 15 | 同一工具的**换说法**（口语、省略、中文数字、相对日期） |
| `boundary` | 15 | 负例与能力边界（不该写 / 该澄清 / 高风险该出卡） |
| `gate` | 4 | 客户端快路径闸门的对照样本 |
| `device` | 3 | 设备能力通道（单向 / 需结果 / 副作用断言） |

> **语料会被删减**：上表是 2026-09-29 实测的 96 条。`docs/testing/T-M7-ai-tool-matrix-2026-09-21.md` 写的是 97 条——多出的那条（`DEV-VOICE-01`）随离线语音能力下架（REQ-75）被删了。**以 `corpus.json` 实测条数为准，不要照抄文档里的数字**，这和下面 6.3 的「桥命令计数」是同一类漂移。

`para` 层是**中文产品独有的价值**：同一个工具「记一笔早餐 12.5」「早饭花了十二块五」「刚吃早饭 12.5」是三种说法，模型的选择准确率在这三种之间会差很多，而这是传统自动化测不到的。

### 1.2 判据与执行分离（`--rejudge`）

判据变了不需要重跑模型：`run.py --rejudge report.json` 直接对已存的报告重算。这一条把「改判据」的成本从 25 分钟降到 0。

四条判据，逐条同时判：

| 判据 | 含义 |
|---|---|
| `hit` | 期望工具出现在本轮工具调用里（`expect` 任一命中即算——近义工具分属两域时任一都正确） |
| `landing` | 写操作的产出形态：`landed`（真落库）/ `produced`（落库或出确认卡有其一）/ `proposal`（只挂起——设备 op 与高风险 op 的**预期**形态）/ `forbidden`（一条写路径都不该有）/ `na` |
| `also` | 附加断言（如记账后应调 `device_widget_refresh` 刷新小组件） |
| `device_expect` | 设备能力的产出是 `device_request` 帧，判据落在帧的 `capability` 名上 |

**★ 三个判据陷阱**（这三个不搞清楚，量到的全是假数据）：

1. **`done.writes` 不是落库数**。它是「写工具的调用次数（含只产出提案的）」。当成落库用，会把设备 op 与高风险 op 一律误判成「已执行」。**落库只看 `executions`**。
2. **落库还是出卡由模型行为决定，不是用例的固定属性**。服务端 `confirm_reason`：`step >= 2` 或低置信 → 强制确认。同一个工具，直接写就落库、先查再写就出卡，**两者都对**。真正的失败形态是**两者皆无**（读了就收尾），所以绝大多数写用例的判据是 `produced` 而不是 `landed`。
3. **前置数据必须覆盖用例里点名的每一个名字**。点名不存在的分类会让 `*_create` 回 `unknown_category`，模型转去读工具，**看起来像「读后不落子」，实际量到的是环境不是模型**。

### 1.3 闸门思维：端到端不通时逐级排查，不要跳着猜

一个功能在设备上不通，按三道闸门逐级查：

| 闸门 | 位置 | 不通的后果 | 怎么开 |
|---|---|---|---|
| ① 客户端 JS | `routeInput`（原 `needsAiRouting`） | 本地快路径把句子吞了，**云端 AI 根本看不到这句输入** | 在线态一律走云端（T18-8 后）；离线态是设计内的快路径 |
| ② 客户端 Kotlin | `AI_AGENT_CLIENT_ENABLED` + 桥流式能力探测 | 整条退回旧单发链路 | 客户端开关打开 |
| ③ 服务端 | `AI_AGENT_ENABLED` + `AI_DEVICE_CHANNEL_ENABLED` | `POST /ai/agent` **恒 404** | `.env` 里把两个开关置 true |

**★ 第一道闸最隐蔽**：它不报错、不留痕，用户只看到「AI 好像变笨了」。历史上为它付过一次大代价——怀疑了模型、怀疑了 prompt、怀疑了桥命令，最后发现客户端 feature flag 默认 false，agent 路径**编译期就不存在**。

### 1.4 归因顺序：先查环境与前置，再谈模型行为

> **测试集自身的缺陷会伪装成模型缺陷，而且伪装得很像。**

「读了就收尾」与「对象不存在」在工具调用序列上**完全一样**（都是一串读工具、零写操作）。第一个案例里有 6 条「读后不落子」，逐条核对后**全是模型的正确行为**——是上一轮的 `create` 用例先读了一次、提案落在 step 2、按审批策略只出确认卡不落库，于是对象从未存在。

由此落地的两条纪律：

- **凡被「改 / 删 / 归档 / 切换」引用的对象一律直接播种**（`corpus.seed`），`create` 用例改用全新名字、作为**终点**，不与改/删目标重叠。
- **加一道离线守卫** `offline_seed_check()`：改/删用例必须点得到一个播种名，且不得指向一个已被更早用例改名的对象（改名链必然「找不到 X」）。守卫现在是 **0 条违规**——改了语料先跑 `--dry-run`，它就是靠这个把问题挡在花模型调用之前。

### 1.5 判据必须能被反向验证（假绿识别）

一个永远不会失败的断言比没有断言更糟，因为它提供**虚假的安全感**。五种本仓库实际踩过的假绿形态：

| 形态 | 反例 | 修法 |
|---|---|---|
| 变异打在不可达分支 | 为「已处置集合」写的用例被投影层的同 id 去重先挡住了，改坏代码仍绿 | 变异必须打在**真实可达路径**；打在不可达分支上证明不了任何事 |
| 自反断言 | 用被测代码自己的输出当期望值 | 期望值必须来自独立来源（真实响应录制 / 手算） |
| 触发点早于被测行为 | 断言在真正那行代码之前就通过了 | 让失败必须发生在被测行**之后**；补中间态断言 |
| 环境替身不真推 | jsdom 里 `scrollTo` 不真的推 `scrollTop` → 距离判据永远「离底很远」 | 让替身真的改变状态；测不到的那一点**写在测试文件头** |
| **断言的是事实源的抄写，不是事实源本身** | `BridgeProtocolTest.test_allCommands_areDefined` 名不副实：它断言一个手写列表有 45 项，不读 `ALL`，于是真实命令集从 45 涨到 83 期间**它一直绿**（详见 6.4） | 断言必须读**规范清单本身**，并交叉核对两侧 |

**纪律**：新增回归用例后，**先把它打回缺陷形态，确认它确实变红，再恢复**。

### 1.6 证据纪律

- **不采信「已完成」这类自我报告**，只采信命令与输出。
- 只有获得证据的检查才能标记为通过；无法确认时写「未能确认」，**不得写成通过**。
- 「用例绿」≠「代码对」；「本地绿」≠「HEAD 绿」≠「CI 绿」。
- 上报缺陷必须带：**复现命令 / 设备 + APK 版本 / 实际观察 / 是否跨轮一致**。**单轮不足以定性**——至少两轮一致，或对可疑用例单独复跑（第一个案例里 `REM-01/02` 三轮三种组合，才判定为「真波动」）。

---

## 二、基础配置清单（一次性投入）

### 2.1 工具链

| 工具 | 版本 / 路径 | 说明 |
|---|---|---|
| JDK | Temurin 17 | Android 构建 |
| Android SDK | `%LOCALAPPDATA%\Android\Sdk` | `platforms;android-36` + `build-tools;36.1.0` |
| adb | `%ANDROID_HOME%\platform-tools\adb.exe` | **不要**用机器 PATH 里另一份旧 adb，版本会乱 |
| Node | **≥ 22**（本项目用 24） | CDP 脚本直接用全局 `WebSocket` + `http`，**不需要任何 npm 依赖** |
| Python | 3.12+（本项目 3.13） | `pip install -e ".[dev]"` |
| Docker Desktop | 任意新稳定版 | 起 DB |
| 模拟器 | AVD `Pixel_7_API_36` | 见 2.4 |

> **CDP 脚本零依赖**这一点很重要：它们只用 Node 内置的 `http` 和全局 `WebSocket`（Node 22+ 原生）。这意味着**发一个 `.mjs` 文件给同事就能跑**，不用 `npm install`、不用交接 node_modules。这是本方案能低成本推广的关键。

### 2.2 服务三件套

| 件 | 命令 | 端口 |
|---|---|---|
| PostgreSQL 18 | `docker compose up -d db` | 5432，库名 `aozora`，账密 `aozora/aozora` |
| FastAPI 服务端 | `uvicorn src.main:app --reload --host 127.0.0.1 --port 8000` | 8000（**起之前先 `netstat` 查端口，8000 常被上一轮的旧进程占着**） |
| Android APK | `.\gradlew.bat :app:assembleDebug` | 产物 `app/build/outputs/apk/debug/app-debug.apk` |

**门禁板（四板）**——每次交付都要报这四个数字：

| 板 | 命令 |
|---|---|
| 类型检查 | `cd app && npm run typecheck` |
| 客户端单测 | `cd app && npm test -- --run` |
| 服务端单测 | `cd server && pytest tests/ -q` |
| Android | `.\gradlew.bat :app:lintDebug :app:testDebugUnitTest :app:assembleDebug :app:assembleDebugAndroidTest` |

### 2.3 开关矩阵（★ 最容易漏的一节）

**AI 路径默认全是关的**。测试前必须逐个确认：

| 开关 | 位置 | 默认 | 不打开的症状 |
|---|---|---|---|
| `AI_AGENT_ENABLED` | `server/.env` | **false** | `POST /ai/agent` 恒 404 |
| `AI_DEVICE_CHANNEL_ENABLED` | `server/.env` | **false** | 设备能力（触感/小组件/分享/权限/选图）不注入，模型看不到这 6 个能力 |
| `AI_AGENT_PROVIDER_API_KEY` | `server/.env` | None | Agent 链路无 key |
| `AI_PROVIDER_API_KEY` | `server/.env` | None | 快路径网关降级 503（**启动不报错，调用时才报**） |
| `AI_AGENT_CLIENT_ENABLED` | APK 构建期 | false | 客户端整条退回旧单发链路 |
| `AI_DAILY_QUOTA_GUEST` | `server/.env` | 20 | 连测时被打断 —— 联调期在 `.env` 里提到 100000（**改配置不改代码**） |

测试实例的核对口径（可直接照抄）：

```
ENV=staging  AI_AGENT_ENABLED=true  AI_DEVICE_CHANNEL_ENABLED=true
AI_DAILY_QUOTA_GUEST=100000  AI_AGENT_PROVIDER_MODEL=deepseek-flash
```

> **服务端单测不受这套开关影响**：`server/tests/conftest.py` 把 AI 相关开关全部**钉死成「关」**，防止本机 `.env` 里的真 key 污染「无 key → 降级」这类断言。所以 `pytest` 不需要 key 也不需要 DB（用 in-memory SQLite）。

### 2.4 设备双端

| | 模拟器 | 真机 |
|---|---|---|
| AVD / 设备 | `Pixel_7_API_36`（`emulator-5554`） | 任意 Android 8+，本项目实测 OnePlus / 小米 HyperOS |
| 装包 | `adb -s emulator-5554 install -r <apk>` | 常规 `adb install -r`；**小米/HyperOS 会被拒 → 改 `adb push` + `adb shell pm install`** |
| 连服务端 | 默认 `http://10.0.2.2:8000/api/v1`（模拟器专用宿主别名） | **必须 `adb reverse tcp:8000 tcp:8000`**（手机与 PC 常不在同一网段） |
| 网络前置 | **必须 `VALIDATED`**，见下 | 一般正常 |
| 稳定性 | 稳，可反复重装 | **USB 会中途掉线**；走查优先模拟器 |

**★ 模拟器网络 `VALIDATED`（不解决会给假结论）**

模拟器 wifi 常是 `INTERNET` 但**缺 `VALIDATED`**（captive portal 探针不可达）→ WorkManager 反复 `Constraints not met` 取消作业，**表现为 UI 显示「已同步」而本地一条都没推**。

```bash
# 先看有没有
adb -s emulator-5554 shell dumpsys connectivity | grep Capabilities
# 没有就禁用探测（最省事）
adb -s emulator-5554 shell settings put global captive_portal_mode 0
adb -s emulator-5554 shell svc wifi disable ; adb -s emulator-5554 shell svc wifi enable
```

备选是起一个返回 204 的探针并把 `captive_portal_http_url` 指过去。**不要同时设 `captive_portal_https_url` 指向一个 HTTP 端点**——会让网络停在 `PARTIAL_CONNECTIVITY` 而不是 `VALIDATED`。

**★ 真机走 `adb reverse` 的两个前提**：

1. debug APK 默认**写死** `10.0.2.2:8000`。要走 reverse 必须**重新构建**：`.\gradlew.bat :app:assembleDebug "-PAOZORA_DEBUG_API_BASE=http://127.0.0.1:8000/api/v1"`
2. 端口必须与 uvicorn 实际监听的一致（用 8011 就得 `adb reverse tcp:8011 tcp:8011`）

`adb reverse` 的增删**只影响新连接** → 改了之后**杀进程重启 app 最可靠**。模拟器重启后 reverse / forward / VALIDATED 设置**全丢，要重来**。

### 2.5 测试资产骨架

要跑起来，需要的不是「一套脚本」，而是这五样资产：

```
server/scripts/ai_tool_matrix/
├── corpus.json        # ★ 测试需求（唯一需要日常维护的文件）
├── run.py             # 服务端执行器（打 /ai/agent）
├── device_flow.mjs    # 设备执行器（CDP 驱动真 UI）
├── report.json        # 逐条明细（--rejudge 的数据源）
└── routes.json        # 离线路由结果
app/src/__tests__/features/
└── ai-tool-matrix-device-gate.test.ts   # 客户端闸门用例
```

---

## 三、启动步骤（Day 1 手册）

> 前提：仓库已 clone，`app/` 下已 `npm ci`（否则 Gradle 的 `preBuild` 会在 Vite 构建那步失败——**`preBuild` 会自动跑 `npm run build` 并把产物同步进 APK assets**）。

### Step 1 · 起数据库

```bash
docker compose up -d db && docker compose ps      # 确认 healthy
```

### Step 2 · 跑迁移

```bash
cd server && alembic upgrade head && alembic current
```

> `alembic.ini` 里的 `sqlalchemy.url` 是**留空**的，运行时从 `DATABASE_URL` 注入。报 "invalid URL" 是环境变量没设，不是 ini 写错。

### Step 3 · 配 `.env` 并起服务端

```bash
cd server && cp .env.example .env      # 首次
# 编辑 .env：DATABASE_URL 必填；要测 AI 就填 key；要测 Agent 再加两个开关
uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

**★ 每次开工必须确认 uvicorn 跑的是当前 HEAD 代码**。沿用几天前的旧进程会让整轮结论建立在旧代码上——本项目为此付过两次代价。多实例并存时，旧进程还会让设备结果**反向**。

```bash
curl http://127.0.0.1:8000/api/v1/health          # liveness
curl http://127.0.0.1:8000/api/v1/health/ready    # readiness（含 DB 探活）
```

### Step 4 · 先跑离线面（0 成本，最快拿到第一批结论）

```bash
cd server && python scripts/ai_tool_matrix/run.py --dry-run
```

这一步不花任何模型调用，秒级返回，直接给出**路由漏注入**与**链式依赖违规**两个核验。**改完语料第一件事就是跑它**——它把问题挡在花钱之前。

### Step 5 · 起设备 + 装 APK

```bash
adb devices -l                                  # 确认目标设备
adb -s emulator-5554 install -r app/build/outputs/apk/debug/app-debug.apk
adb -s emulator-5554 reverse tcp:8000 tcp:8000
adb -s emulator-5554 shell dumpsys connectivity | grep Capabilities   # 见 2.4
adb -s emulator-5554 shell am start -n com.aozora.app/.MainActivity
```

> **★ 命令一律带 `-s emulator-5554`**。同时插着真机时，裸 `adb install` 会**误装到真机**。

### Step 6 · 建 CDP 通路（设备自动化的入口）

```bash
# ① 取 app 的 PID 并一步转发到本机端口（PID 每次启动都会变）
adb -s emulator-5554 forward tcp:9222 \
  localabstract:webview_devtools_remote_$(adb -s emulator-5554 shell pidof com.aozora.app)
# ② 验证：应返回 JSON 数组，其中一项 title 为 "Aozora"
curl http://127.0.0.1:9222/json/list
```

> 想确认 socket 确实存在时，用 `adb -s emulator-5554 shell "cat /proc/net/unix | grep webview_devtools_remote"`——但**取 PID 用 `pidof` 更直接**，不必去解析那一行。
>
> **`am force-stop` + 重启 app 后 forward 会失效**，必须按新 PID 重新转发再连。

**为什么 debug 构建就能用 CDP（2026-09-29 实测确认）**：

本项目源码里**没有**任何 `WebView.setWebContentsDebuggingEnabled(...)` 调用（全仓 grep 零命中），但 CDP 通路照常可用。实测证据：

```
$ adb -s emulator-5554 shell dumpsys package com.aozora.app | grep flags=
    flags=[ DEBUGGABLE HAS_CODE ALLOW_CLEAR_USER_DATA ]      ← 包是可调试的
$ adb -s emulator-5554 shell "cat /proc/net/unix | grep webview_devtools_remote"
    ... @webview_devtools_remote_18940                        ← socket 存在
$ curl http://127.0.0.1:9223/json/list
    [ { "title": "Aozora", "type": "page", ... } ]            ← 能拿到页面
```

**结论**：**WebView 远程调试对 `android:debuggable=true` 的包是自动开启的**，不需要在代码里显式调用。debug 构建由 AGP 自动带上该标记，所以这条路直接可用。

**反过来的两条推论，决定了整套设备自动化的适用范围**：

- **release APK 走不通这条路**——它不可调试，socket 不会出现。
- **如果某天 `/json/list` 返回空或连不上**，第一件事是查 `flags=` 里有没有 `DEBUGGABLE`（而不是去改脚本）。确认是可调试包却仍无 socket 时，再在 `MainActivity.onCreate` 里补 `WebView.setWebContentsDebuggingEnabled(true)`（包在 `if (BuildConfig.DEBUG)` 里）后重装。这是一个「知道怎么兜底」胜过「记住某一行代码」的例子。

### Step 7 · 跑设备面

```bash
node server/scripts/ai_tool_matrix/device_flow.mjs            # 默认 10 条高价值子集
node server/scripts/ai_tool_matrix/device_flow.mjs TX-ADD-01  # 指定用例
CDP_PORT=9223 node server/scripts/ai_tool_matrix/device_flow.mjs   # 换端口（多设备并行）
```

### Step 8 · 跑服务端全量（按需）

```bash
cd server && AOZORA_BASE=http://127.0.0.1:8000/api/v1 python -u scripts/ai_tool_matrix/run.py
# 只跑一域（约 2 分钟）
python scripts/ai_tool_matrix/run.py --domain savings --layer boundary
```

### Step 9 · 客户端闸门用例

```bash
cd app && npx vitest run src/__tests__/features/ai-tool-matrix-device-gate.test.ts
```

---

## 四、日常运转：只更新测试需求

**这是整套配置的目的**。一轮完整回归只有四步：

```
① 改 corpus.json          ← 唯一需要人写的东西
② run.py --dry-run        ← 秒级，核验路由与链式依赖
③ run.py（服务端全量）      ← 量模型层
④ device_flow.mjs <子集>   ← 量可达性与渲染
```

### 4.1 加一条用例

```json
{ "id": "SAV-CREATE-02", "layer": "core", "text": "存钱买台相机 8000",
  "expect": ["savings_create"], "landing": "produced" }
```

三条自检（对应 1.2 的三个陷阱）：

1. 这条用例点名的名字，在 `corpus.seed` 里**存在吗**？（点名不存在的对象 → 量到的是环境）
2. 这是**写**用例吗？判据该是 `produced` 而不是 `landed`（除非它是「单发、无前置读取」的快路径用例才用 `landed`）。
3. 它跟更早的用例**撞名字**吗？（撞名会被模型正确地拒绝，然后被记成失败）

### 4.2 改判据（不花钱）

```bash
cd server && python scripts/ai_tool_matrix/run.py --rejudge scripts/ai_tool_matrix/report.json
```

判据/期望值变了直接重算，**不重跑模型**。

### 4.3 加一个域 / 加一个工具

工具注册表在 `server/src/modules/ai/tools.py`。加工具时的**连带面**（这个项目踩过，一次漏一处）：

1. `tools.py` 注册表（含 Draft-07 `parameters`）
2. 域路由词表 `routing.py` 的 `_SIGNALS`
3. Skill 文档 `skills.py`（**漏写 Skill 是静默的**）
4. 执行器接线（服务端 or 设备端）
5. `corpus.json` 加至少一条 `core` 用例
6. 契约 fixture（v1 / v2 两套）

**单域上限 8 个工具**——超过 8 个，模型的选择准确率显著下降。这条是实测结论，不是经验之谈。

### 4.4 一轮完整回归的产出

- `report.json`：逐条明细（**这是交付物，不是中间产物**）
- 通过率 + 按域拆解
- 未通过逐条：输入 / 实际 / 判定 / 归属（客户端 or 服务端）
- **跨轮一致性**：哪些结论跨轮稳定，哪些是波动

> **★ 缺陷清单必须合并三个测量面**。只看服务端那 4 条会**严重低估**问题——因为设备上还有一道闸门挡在 AI 前面，那里的问题一次都没进过服务端测试集。合并后是 6 类。

---

## 五、双端走查配方

### 5.1 CDP 通路（两端通用）

见 Step 6。**设备面和服务端面最大的差别在这里**：`run.py` 直打 HTTP 端点，绕开了客户端；`device_flow.mjs` 走的是**真实 WebView 里的真实 UI**。

### 5.2 页面内注入 JS 的六条配方

CDP 的 `Runtime.evaluate` 是把 JS 送进页面执行。六条踩出来的配方：

| 配方 | 为什么 |
|---|---|
| **点击必须走 `Input.dispatchMouseEvent`，不要用 `el.click()`** | `el.click()` 在 WebView 里触发不了某些合成事件（React 合成事件、hover 态）；CDP 的 Input 域走的是 Chromium 真实输入管线，等价于一次真触摸 |
| **点击前先 `scrollIntoView({block:'center'})`** | 视口外的元素坐标算出来在屏幕外，`dispatchMouseEvent` 直接打空 |
| **React 受控 input 必须用 native setter** | 直接 `el.value = x` 不会触发 React 的 onChange（React 在 value 上装了 descriptor 做劫持）。要 `Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(el, x)`，再手工派发 `new Event('input', {bubbles:true})` |
| **JS 路由是 hash 路由，用 `location.hash = '#/assistant'` 导航** | 不能用路径导航；**Playwright 视觉基线就因为用路径导航，导致认证页 20 张基线全是首页** |
| **`reload` 后页内记录器全丢** | 注入的全局变量、事件钩子在 reload 后不存在。曾有整轮误判「没走 agent」，就是踩了这条 |
| **断言优先读 `localStorage`，不要读 DOM** | zustand persist 把整个 store 序列化到 `aozora-*` key。**DOM 是视图投影，localStorage 是事实源**——读它比读 DOM 稳。前端打 `data-testid`（如 `v2-confirmation-card`）则是**UI 与测试的解耦契约**，靠 testid 定位比靠文案稳 |

**★ 验一类行为要能区分两种可能**：验证「进入会话即在最新消息、无滚动动画」时，用 `requestAnimationFrame` 采样 `scrollTop`——36 条消息的会话下第 1 帧即 1852（=`scrollHeight - clientHeight`），其后 89 帧恒定、无中间值。**再做一次灵敏度对照**：手动 `scrollTo({behavior:'smooth'})` 采样到 **43 个不同值**。两种行为在采样上截然可分，才排除了「测量方法本身分辨不出」的假绿。

### 5.3 绕过 UI 直调原生桥（测桥方法的最小配方）

有些东西不必走 UI——直接调原生桥更快、更稳。关键难点是**桥的响应是多路复用的**：所有响应都走同一个回调 `__aozoraBridgeRespond`，所以必须**按 requestId 过滤**，并**链式保留原有回调**（否则会冲掉前端自己注册的那个）。

```
注入一个 Promise 包装：劫持 __aozoraBridgeRespond
  → 回调里判断 r.requestId === 本次 rid
     命中 → resolve，并还原 prev
     未命中 → 透传给 prev
  → 超时也要还原 prev（防御性）
  → 然后 window.AozoraBridge.call(1, rid, "<命令>", <payload>)
```

**适用范围与前提**：只能调**桥白名单内**的命令；调用前先确认「桥计数」不变量没有被改过（见 6.3）。

**备用通道**：debug 构建里还有一个只走 adb、**完全不依赖 WebView** 的桥入口——一个 debug-only 的广播接收器：

```bash
adb shell am broadcast -a com.aozora.app.BRIDGE_COMMAND \
  --es command "transaction.insert" --es payload "<base64 的 JSON>"
```

它比 CDP 稳（不受 WebView 状态影响）但更粗（拿不到页面上下文）。**两条通道互为兜底**：CDP 拿不到结果时，先用广播确认「桥本身是好的」，就能立刻把问题二分到「桥坏了」还是「页面驱动坏了」。注意它只存在于 debug 变体，release 包里没有，且设了签名级权限——第三方应用够不到，`adb shell` 例外。

### 5.4 两条取证通道（互补，别只用一个）

| 通道 | 抓到的层次 | 什么时候用 |
|---|---|---|
| **CDP `Network` 域** | Chromium 网络栈层，**含 SSE 帧** | 看流式响应、看真实请求体/响应体 |
| **页内 monkey-patch `window.fetch`** | **应用层**，即 React 状态机处理后的形态 | 看业务错误（如 422）最直接；注意要 `res.clone().text()` 才能取到正文而不消费掉流 |

抓不到 / 数据不对时，先确认**服务端日志里到底有没有你这次的请求**——多实例并存时，你打到的可能不是你以为的那个实例。

### 5.5 模拟器 vs 真机的差别（走查前必读）

| 维度 | 差别 | 影响 |
|---|---|---|
| 网络 | 模拟器可能缺 `VALIDATED` | 同步静默不跑，UI 却说「已同步」 |
| 连通方式 | 真机必须 `adb reverse` | 端口不一致 → 全部同步失败 |
| 装包 | 小米/HyperOS 拒 `adb install` | 改 `push` + `pm install` |
| 稳定性 | 真机 USB 会中途掉线 | 走查优先模拟器，真机只做最终确认 |
| **字体度量** | ★ **Android 的 `tabular-nums` 比桌面宽 4.6~6.3%**（中文≈一致） | 桌面 Playwright 基线**不能**代替设备侧布局判定 |
| 渲染 | Playwright 是 Chromium 渲染，设备是 WebView | 两者**互补而非等价**——32 页 × 4 主题靠 Playwright，字体/光栅靠设备 |

**取证纪律**：共享模拟器被占用时不导航，用 CDP 插一个 offscreen 元素测宽度。

### 5.6 真机专项：设备内数据取证

- **模拟器**上 `run-as` 能直接调 `sqlite3` 查库：`adb exec-out "run-as com.aozora.app sqlite3 databases/aozora.db '<SQL>'"`
- **真机上 `sqlite3` 被禁**（小米/HyperOS 实测），只能把库文件整个拉出来再本地查。**这时必须用 `exec-out` 而不是 `shell`**：

  ```bash
  adb exec-out "run-as com.aozora.app cat databases/aozora.db" > f.db
  ```

  `adb shell cat` 会做换行转换，拉出来的文件**直接是 `database disk image is malformed`**。还要**连 `-wal` / `-shm` 一起拉到同一目录**，否则读到的是落后于 WAL 的旧值——本项目曾据此误判一个游标为 0，实际是 574。

- **Room 的列名是 camelCase**（`bookId` / `amountCents` / `bizDate` / `deletedAt`），不是 SQL 惯用的 snake_case，查库时别写错
- 页内存储（localStorage / IndexedDB）用 CDP 在页面里 eval 读即可，比拉库快
- 清数据造「新账号」：`adb shell pm clear com.aozora.app`——**会清掉本地库 + 偏好设置，动手前先确认不是别人的真机**

---

## 六、边界与坑清单

### 6.1 这套方法的边界（先划清楚，避免过度承诺）

| 边界 | 说明 |
|---|---|
| **release APK 不可自动化** | CDP 需要 debug 包；设备自动化只覆盖 debug 构建 |
| **物理真机未全覆盖** | 本项目多轮设备回归实际在模拟器完成，触屏手感差异未覆盖 |
| **无人值守的环境限制** | 提醒类能力在无人值守下只能验到第一步（系统权限框会超时），完整路径只能人工验 |
| **模型有非确定性** | 单轮结果不足以定性；稳定结论至少两轮一致 |
| **视觉基线 ≠ 设备渲染** | Playwright 是 Chromium，设备是 WebView，两者互补 |
| **jsdom 测不到布局时序** | `useLayoutEffect` vs `useEffect` 的差别只有设备侧证据——已在测试文件头写明 |

### 6.2 坑清单（按测试同事最容易踩排序）

1. **模拟器缺 `VALIDATED`** → 同步静默不跑，UI 显示「已同步」（见 2.4）
2. **AI Agent 默认关** → `/ai/agent` 404（**三个开关都要开**，见 2.3）
3. **沿用旧 uvicorn 进程** → 结论建立在旧代码上；多实例还会让设备结果反向
4. **`adb reverse` 端口与 uvicorn 不一致** → 全部同步失败
5. **debug APK 写死 `10.0.2.2`** → 想走 reverse 必须重建
6. **`adb` 命令没带 `-s`** → 误装到真机
7. **模拟器重启后 reverse / forward / VALIDATED 全丢** → 每次重启重来
8. **`pm clear` 清了别人的真机** → 动手前确认设备归属
9. **gate guest 配额 20** → 连测被打断；在 `.env` 里提额，**不改代码**
10. **`AI_TIMEOUT_SECONDS` 上限 12.0** → 配大了直接 `ValidationError` 起不来
11. **`server/.env` 不进仓库** → 首次必须 `cp .env.example .env`
12. **改了 prompts / 路由词表** → 必须重启 uvicorn 才生效
13. **服务端日志里没有你这次的请求** → 说明打到了别的实例上（多实例并存时先查这个）
14. **`/json/list` 连不上** → 先查包是不是 `DEBUGGABLE`（`dumpsys package <包名> | grep flags=`），别急着改脚本（见 Step 6）

### 6.3 三条不变量：每次改动必核

判断「我这次动没动到跨层契约」，看这三个数——任一变化都要在交付记录里显式声明：

| 不变量 | 事实源 | 为什么危险 |
|---|---|---|
| **桥命令计数** | 桥命令白名单的 `ALL.size` | 前后端两侧各自维护，漏改一侧即静默失配 |
| **Room 数据库版本** | `app/schemas/` 下的 schema json | 版本没升就改表 → 老用户升级后崩 |
| **meta 白名单键数** | `MetaWhitelist.KEYS` | 多一个键 = 多一条能被 AI 改写的偏好 |

> **★ 不要相信文档里的计数，要实测**。本项目这个数字从 85 漂到过 83，两份不同时期的文档各写着一个数。**至少三处同步**：Kotlin 侧命令表 / TypeScript 侧 `native-bridge` / Kotlin 桥测试里的子集白名单断言。

### 6.4 跨层契约：改一处必须同时改的四处

这个项目历史上最严重的回归都属于这一类——**同一契约在多个位置必须保持一致，改动任一处就会静默失配**：

| 契约 | 同步点 | 事实源 |
|---|---|---|
| **JS 桥命令集** | Kotlin 命令表 / TypeScript `native-bridge.ts` / 桥命令**白名单** / 漂移检测用例 | `BridgeCommands.ALL`（实测 **83** 条）—— 检测在 `SecurityConfigTest`，它读 `ALL` 并与 TS 侧交叉核对 |
| AI 工具名 | 工具注册表 / 域路由 / Skill / 执行器 | 必须**下划线**，不能点号——点号会让 provider 返回 400 `invalid_request_error`，整轮请求死掉 |
| 金额符号 | 客户端记账 / 服务端统计 / 导出 | 收入 `+`、支出 `−`，是**隐性跨端契约** |
| 契约 op 注册表 | v1 / v2 两套 fixture | 新增 op 两套都要加 |

**纪律**：此类契约**每一处都要有测试钉住**，且改动任一处即使测试变红。只在一处设防等于未设防。

> **★ 这里有一个现成的反面教材，值得当案例讲。** `BridgeProtocolTest.test_allCommands_areDefined` 这个用例名承诺「所有命令都已定义」，但它实际断言的是**一个手写字面量列表有 45 项**——不来自 `BridgeCommands.ALL`。于是真实的命令集从 45 涨到 83 的过程中，**它一直是绿的**。
>
> 两个教训：
> 1. **断言的必须是事实源本身，不是事实源的一份抄写**。抄写会漂移，而且漂移时不报错。
> 2. **测试的名字是对读者的承诺**。叫 `test_allCommands_areDefined` 却只覆盖一部分，比不写这个测试危害更大——它让后来的人以为这块已经被守住了。
>
> 正确的形态就是隔壁的 `SecurityConfigTest`：读 `BridgeCommands.ALL`（规范清单，注释里写明「新增命令必须追加到这里」），再与 TS 侧交叉核对。

---

## 七、迁移到你自己的项目

### 7.1 最小可运行版本（一周内）

不必一次建成三层测量面。**先做服务端面 + 离线面**，它们成本最低、收益最大。

- [ ] **写 `corpus.json`**：先覆盖主干说法（`core` 层），20–30 条即可开工
- [ ] **写 `run.py`**：一个函数发一条请求、一个函数判一条结果。**判据与执行分离**（支持 `--rejudge`）
- [ ] **加 `--dry-run`**：把「前置数据是否齐」「链式依赖是否自洽」做成 0 成本核验
- [ ] **建 `.env` 开关表**：把所有 feature flag 列成一张表，写清「不开的症状」——这张表能省掉大量「功能没反应」的排查时间
- [ ] **建四板基线**：定下你的四个数字与命令，每次交付都报

### 7.2 加上设备面（第二周）

- [ ] 确认你的 App **debug 包能开 WebView 调试**；拿到 `/json/list` 返回你的页面
- [ ] 把「找 socket → forward → 连 WS → eval → 截图」抽成一个**零依赖** `.mjs`
- [ ] 封装四个原语：`clickText` / `typeText`（native setter）/ `navigate`（hash）/ `waitFor`
- [ ] 让设备执行器**读同一份 `corpus.json`**，只跑「必须真机才能验」的子集

### 7.3 立纪律（持续）

- [ ] **每修一个缺陷 → 变成一条自动用例**（回归集合来自真实咬痕，不是凭想象编写）
- [ ] **每个新用例先反向验证它确实会红**
- [ ] **每轮收口把「今天有什么值得记的」写进 memory**：真实踩过的坑 / 反直觉的发现 / 会重犯的错
- [ ] **失败归因先分三层**：前置阻塞 → 环境 flaky → 代码回归；**不要默认「红 = 回归」**

### 7.4 一句话交接给同事

> 你只需要会两件事：**往 `corpus.json` 里加一条 JSON**，和**看懂 `report.json` 里的判定**。
> 环境怎么起、探针怎么打、判据怎么算，都已经固化成命令了。

---

## 附录 A · 命令速查

```bash
# —— 环境 ——
docker compose up -d db && docker compose ps
cd server && alembic upgrade head
cd server && uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
.\gradlew.bat :app:assembleDebug

# —— 离线面（0 成本，最先跑）——
cd server && python scripts/ai_tool_matrix/run.py --dry-run
cd app && npx vitest run src/__tests__/features/ai-tool-matrix-device-gate.test.ts

# —— 服务端面 ——
cd server && python -u scripts/ai_tool_matrix/run.py                     # 全量 ~25 min
cd server && python scripts/ai_tool_matrix/run.py --domain savings        # 单域 ~2 min
cd server && python scripts/ai_tool_matrix/run.py --rejudge scripts/ai_tool_matrix/report.json

# —— 设备面 ——
adb devices -l
adb -s emulator-5554 install -r app/build/outputs/apk/debug/app-debug.apk
adb -s emulator-5554 reverse tcp:8000 tcp:8000
adb -s emulator-5554 shell dumpsys connectivity | grep Capabilities
adb -s emulator-5554 forward tcp:9222 \
  localabstract:webview_devtools_remote_$(adb -s emulator-5554 shell pidof com.aozora.app)
node server/scripts/ai_tool_matrix/device_flow.mjs

# —— 四板 ——
cd app && npm run typecheck && npm test -- --run
cd server && pytest tests/ -q && ruff check src/ tests/
.\gradlew.bat :app:lintDebug :app:testDebugUnitTest :app:assembleDebugAndroidTest

# —— 三条不变量自检（见 6.3）——
grep -c 'const val [A-Z_0-9]* = "' app/src/main/java/com/aozora/app/bridge/BridgeProtocol.kt   # 桥命令数
grep -n "version = " app/src/main/java/com/aozora/app/db/AozoraDatabase.kt                        # Room 版本
grep -c "=" app/src/main/java/com/aozora/app/bridge/MetaWhitelist.kt                              # meta 白名单键数

# —— 备用桥通道（不经 WebView）——
adb shell am broadcast -a com.aozora.app.BRIDGE_COMMAND --es command "bridge.getVersion" --es payload "e30="

# —— 清场 ——
adb -s emulator-5554 shell pm clear com.aozora.app
```

## 附录 B · 语料条目模板

```json
{
  "id": "<域缩写>-<动作>-<序号>",
  "domain": "transaction",
  "layer": "core | para | boundary | gate | device",
  "kind": "read | write | boundary | device",
  "text": "<用户会说的原话>",
  "expect": ["<期望工具名>"],
  "landing": "landed | produced | proposal | forbidden | na",
  "also": ["<可选的附加断言，如 device_widget_refresh>"],
  "note": "<为什么这么判，写给自己三个月后看>"
}
```

> `also` 是可选字段（当前 96 条里只有 `DEV-SIDE-01` 用了它）。`note` 强烈建议写——第一个案例里最好用的几条经验都留在这个字段里。

## 附录 C · 术语对照

| 术语 | 含义 |
|---|---|
| 测量面 | 一层测试执行环境（离线 / 服务端 / 设备） |
| 语料库 | `corpus.json`，测试需求的唯一载体 |
| 判据与执行分离 | 改判据不重跑执行（`--rejudge`） |
| 闸门 | 端到端通路上的逐级检查点（客户端 JS / 客户端 Kotlin / 服务端） |
| 假绿 | 用例绿，但没盯住目标代码 |
| 变异 | 故意改坏目标代码，验证用例会变红 |
| 链式依赖 | 后面的用例依赖前面用例产出的对象 |
| 落库 / 出卡 | 写操作的两种正确产出形态；**两者皆无才是失败** |
| 四板 | 类型检查 / 客户端单测 / 服务端单测 / 集成测试 |
| 播种 | 测试前把用例要点名的对象预先建好（`corpus.seed`） |

---

*基于 Aozora 项目 T17–T20 轮次实战提炼。文中每一条「★」都对应一次真实事故或一次被推翻的结论。*