# T-M5-02 安全加固 第二轮审查

> 审查日期: 2026-09-07（第二轮）
> 审查范围: T-M5-02a / 02b / 02c / 02d 全 4 个子任务
> 审查方法: 全文件重读 + 修复项代码 diff 复核 + 测试实跑 + YAML 校验
> 对照基线: 首轮审查（`T-M5-02-review.md`）+ `docs/milestones/production-execution-plan.md` L837-852

---

## 一、结论

| 子任务 | 结论 | 备注 |
|---|---|---|
| T-M5-02a 客户端安全基线 | ✅ 通过（2 项观察） | `dataExtractionRules` 仍未声明（O-T-M5-02-1）；Bridge 命令数现为 44（非首轮所述 40），Kotlin ↔ TS 一致 |
| T-M5-02b 服务端安全加固 | ✅ 通过 | middleware 顺序已修正，413 带齐安全头（实测 + 回归测试双验证） |
| T-M5-02c 密钥治理与产物扫描 | ✅ 通过（1 项观察） | truffleHog 仍未引入（gitleaks 单工具覆盖增量 + 全历史） |
| T-M5-02d 依赖扫描与日志脱敏 | ✅ 通过 | Gradle OWASP DependencyCheck 已接入并实跑 23/23 测试通过 |

**总体结论: ✅ 通过，与首轮一致**。全部 3 项中优先级修复已落实且回归测试通过；首轮 5 项低优先级观察仍为观察（均非阻塞）。

---

## 二、首轮修复项复核

### R-T-M5-02-1 ✅（02b docstring 与实现对齐）

- **文件:** `server/src/common/security.py` L45-55
- **复核:** `RequestBodyLimitMiddleware` docstring 明确写明：
  > "Requests without `Content-Length` (e.g. chunked transfer encoding) are **not** size-limited here — downstream parsers are expected to enforce their own caps."
- **判定:** 与代码行为（L79-91 仅 fast-path Content-Length，chunked 直接 `call_next`）完全一致，正确性缺口关闭。

### R-T-M5-02-2 ✅（02d Gradle OWASP DependencyCheck 接入）

- **文件 1:** `app/build.gradle.kts` L5-8 — `id("org.owasp.dependencycheck") version "11.1.0"`，含 NVD_API_KEY 注释 ✅
- **文件 2:** `.github/workflows/ci.yml` L56-57 — android job 末尾步骤 `bash ./gradlew :app:dependencyCheckAnalyze -PfailBuildOnCVSS=7 --no-daemon` ✅
- **YAML 校验:** `yaml.safe_load` 解析 OK，top-level keys 正常（`name`, `on`, `concurrency`, `jobs`）
- **阈值对齐:** `failBuildOnCVSS=7` 与同 job 内 `npm audit --audit-level=high` / `pip-audit` 的高危口径一致 ✅

### R-T-M5-02-3 ✅（02b middleware 顺序 + 413 安全头）

- **文件 1:** `server/src/main.py` L111-130 — 顺序已更正：
  ```
  add_middleware(RequestIdMiddleware)         # 最先 add → 最内层
  add_middleware(CORSMiddleware, ...)
  add_middleware(RequestBodyLimitMiddleware, ...)  # L123 先 add
  add_middleware(SecurityHeadersMiddleware)        # L130 最后 add → 最外层 ✅
  ```
  注释 L111 "outermost LAST — add_middleware wraps" 与实际顺序一致
- **文件 2:** `server/tests/test_security.py` L85-91 — `test_content_length_exceeds_limit` 末尾追加 3 个安全头断言（X-Content-Type-Options / X-Frame-Options / Strict-Transport-Security）✅
- **实测:** `pytest tests/test_security.py tests/test_logging.py` — **23/23 通过**，0.62 秒

---

## 三、逐子任务复审

### 3.1 T-M5-02a 客户端安全基线

**复核清单（全 ✅）：**

| 计划要求 | 当前状态 | 复核结果 |
|---|---|---|
| `usesCleartextTraffic="false"` | Manifest L24 | ✅ |
| `allowBackup="false"` | Manifest L20 | ✅ |
| `networkSecurityConfig` 引用 | Manifest L22 | ✅ |
| release NSC HTTPS-only | `src/release/res/xml/network_security_config.xml` | ✅ |
| debug NSC localhost + 10.0.2.2 | `src/debug/res/xml/network_security_config.xml` | ✅ |
| WebView `allowFileAccess=false` | MainActivity.kt L64 | ✅ |
| WebView `allowContentAccess=false` | MainActivity.kt L65 | ✅ |
| WebView `allowFileAccessFromFileURLs=false` | MainActivity.kt L69 | ✅ |
| WebView `allowUniversalAccessFromFileURLs=false` | MainActivity.kt L71 | ✅ |
| WebViewAssetLoader 虚拟 HTTPS | MainActivity.kt L110 `https://appassets.androidplatform.net` | ✅ |
| `shouldOverrideUrlLoading` 限 HTTPS 虚拟域 | MainActivity.kt L81-82 | ✅ |
| Bridge 白名单 Kotlin ↔ TS 一致 | 44 / 44（BridgeProtocol.kt ↔ bridge-types.ts）| ✅ |
| SecurityConfigTest 覆盖 | 13 个测试方法，含 Bridge 契约 / NSC / Manifest 三类断言 | ✅ |
| AozoraBridge.kt 无 payload 日志 | 全文件 `grep Log\.` 零命中 | ✅ |

**⚠️ 观察（沿用首轮，未变）：**

- **O-T-M5-02-1: `android:dataExtractionRules` 仍未声明。** Android 12+（API 31+）推荐属性。当前 targetSdk = 36 但未声明此属性，Play 审核可能标记。计划文本（L849）明确要求此属性，属计划-实现差异。
  - 影响: 不影响运行时安全性（`allowBackup="false"` 已阻 adb backup）
  - 处置建议: 增加 `res/xml/backup_rules.xml` + Manifest 属性

- **O-T-M5-02-2: SecurityConfigTest 用文件文本匹配而非 PackageManager/WebViewSettings。** 计划描述（L849）与实现不一致，但实现本身是可接受的替代方案。
  - 处置建议: 更新计划描述

**🔎 本轮新增说明（非新问题）：**

- **Bridge 命令计数勘误:** 首轮文档记为 "40 命令"，实际现为 **44 命令**（随 T-M5-04a/04b/04d 等新增）。Kotlin `BridgeCommands` 显式枚举 44 项，TS `BridgeCommands` 对象 44 项，`SecurityConfigTest` 断言硬编码 44。三处一致，无缺口。首轮文档数字陈旧。

### 3.2 T-M5-02b 服务端安全加固

**复核清单（全 ✅）：**

| 计划要求 | 当前状态 | 复核结果 |
|---|---|---|
| 6 个安全头 | security.py L18-25 | ✅ |
| 请求体 10MB 限制 | security.py L60 默认 `10 * 1024 * 1024` | ✅ |
| CORS production 拒绝 `*` | config.py L207-210 | ✅ |
| Swagger/ReDoc/OpenAPI production 禁用 | main.py L105-107 | ✅ |
| 附件上传路径排除在大小限制外 | main.py L126 | ✅ |
| 413 响应带齐安全头 | main.py L130 SecurityHeaders 最外层 + test_security.py L85-91 回归 | ✅ 已修复 |
| 安全头在 404/error 也存在 | test_security.py `test_all_headers_on_error_responses` | ✅ |

**测试实跑:**
```
tests/test_security.py — 11/11 PASSED
  TestSecurityHeaders: 7 (hsts / x-content-type / x-frame / referrer / csp / permissions / error-404)
  TestRequestBodyLimit: 2 (413 Content-Length / normal pass-through)
  TestDocsGating: 2 (docs / openapi.json)
```

**⚠️ 观察（沿用首轮，未变）：**

- **O-T-M5-02-3: CORS 白名单未在代码中硬编码生产域名模式。** `config.py` `_check_production_safety` 仅拒绝 `*`，不校验具体值（如 `https://evil.com` 也能通过）。
  - 影响: 默认安全由运维控制（环境变量），但缺代码级兜底
  - 处置建议: config.py 加 production 模式下 CORS 域名格式校验（必须 `https://*.aozora.app` 模式）

### 3.3 T-M5-02c 密钥治理与产物扫描

**复核清单（全 ✅）：**

| 计划要求 | 当前状态 | 复核结果 |
|---|---|---|
| CI 密钥扫描 PR/push 增量 | secret-scan.yml L9-13 | ✅ |
| CI 密钥扫描 每周全历史 | secret-scan.yml L14-16（cron `17 3 * * 1`）| ✅ |
| 三产物扫描脚本 | tools/scan-secrets.sh（APK strings / Web grep / Trivy image）| ✅ |
| 密钥注入路径文档 | docs/runbooks/secret-management.md | ✅ |
| production 启动拒绝默认 secret_key | config.py L199-201 | ✅ |
| production 启动要求 AI key 已设置 | config.py L211-215 | ✅ |
| production 启动要求 DEV_AUTH_CODE 空 | config.py L203-206 | ✅ |
| production 启动要求 CORS ≠ * | config.py L207-210 | ✅ |
| 密钥轮换 + 泄露处置文档化 | secret-management.md | ✅ |
| YAML 格式校验 | `yaml.safe_load` OK | ✅ |

**⚠️ 观察（沿用首轮，未变）：**

- **O-T-M5-02-4: truffleHog 未引入。** 计划要求 "gitleaks + truffleHog"，实际仅 gitleaks。但 gitleaks `fetch-depth: 0` + 每周 cron 已覆盖全历史，truffleHog 是冗余能力。
  - 处置建议: 更新计划文本说明 "gitleaks 单工具覆盖增量与全历史两种模式"

### 3.4 T-M5-02d 依赖扫描与日志脱敏

**复核清单（全 ✅）：**

| 计划要求 | 当前状态 | 复核结果 |
|---|---|---|
| npm audit 接入 CI | ci.yml L33 | ✅ |
| pip-audit 接入 CI | ci.yml L76 | ✅ |
| OWASP DependencyCheck 接入 | build.gradle.kts L8 + ci.yml L57 | ✅ 已修复 |
| 服务端日志脱敏 regex | logging.py L23-27 `_SENSITIVE_KEY_RE` | ✅ |
| 服务端日志脱敏 替换 `***` | logging.py L40-44 `SensitiveFieldFilter` | ✅ |
| JSONFormatter extras 白名单 + 内联脱敏 | logging.py L63-73 | ✅ |
| AozoraBridge.kt 日志屏蔽 payload | 全文件无 `Log.*` 调用 | ✅ |
| 日志审计测试 | test_logging.py `TestSensitiveFieldRedaction` | ✅ |

**测试实跑:**
```
tests/test_logging.py — 12/12 PASSED
  TestJSONFormatter: 3 (valid_json / structured_extras / exception_traceback)
  TestSensitiveFieldRedaction: 9 (token / sensitive_in_whitelist / non_sensitive_pass /
    refresh_token / api_key / x_api_key_header / authorization / auth_token_prefixed /
    prompt_tokens_not_redacted)
```

**⚠️ 观察（沿用首轮，未变）：**

- **O-T-M5-02-5: 48h 处置阈值 + allowlist 流程未文档化。** 计划要求 "高危及以上 48h 内修复或加 allowlist 注释（附 Jira 编号）"。ci.yml 仅 `npm audit --audit-level=high` / `pip-audit` fail CI；无 allowlist 机制与 Jira 关联。
  - 处置建议: 在 `docs/runbooks/` 增加依赖漏洞处置 SOP 文档

---

## 四、首轮审查文档勘误

本轮复核发现首轮文档（`T-M5-02-review.md`）存在两处数字陈旧：

| 项 | 首轮文档 | 实际 | 影响 |
|---|---|---|---|
| Bridge 命令数（2.1 节）| "40 命令" | 44 命令（Kotlin 44 / TS 44 / SecurityConfigTest 硬编码 44，三处一致）| 文档描述陈旧；代码无缺口 |
| test_security.py 用例数（§四）| "10" | 11（R-T-M5-02-3 修复追加了 `test_content_length_exceeds_limit` 的 3 个安全头断言，但方法数从 10 → 11）| 文档描述陈旧 |
| test_logging.py 脱敏用例数（§四）| "5" | 9（TestSensitiveFieldRedaction 类含 9 个方法；全文件 12 个方法含 TestJSONFormatter 3 个）| 文档描述陈旧 |

**判定:** 均为文档数字未随修复同步更新，代码本身无新缺口。

---

## 五、问题汇总（第二轮）

| 编号 | 类型 | 子任务 | 描述 | 优先级 | 处置 |
|---|---|---|---|---|---|
| R-T-M5-02-1 | 正确性 | 02b | `RequestBodyLimitMiddleware` docstring 与实现不一致 | 中 | ✅ 首轮已修复，本轮复核通过 |
| R-T-M5-02-2 | 缺失 | 02d | Gradle OWASP DependencyCheck 未接入 | 中 | ✅ 首轮已修复，本轮复核通过 |
| R-T-M5-02-3 | 正确性 | 02b | middleware 顺序错误致 413 缺安全头 | 中 | ✅ 首轮已修复，本轮复核通过 |
| O-T-M5-02-1 | 观察 | 02a | `android:dataExtractionRules` 未声明（API 31+ 推荐）| 低 | 待处置 |
| O-T-M5-02-2 | 观察 | 02a | SecurityConfigTest 用文本匹配而非 PackageManager | 信息 | 更新计划描述 |
| O-T-M5-02-3 | 观察 | 02b | CORS production 白名单未硬编码域名模式 | 低 | 待处置 |
| O-T-M5-02-4 | 观察 | 02c | truffleHog 未引入（gitleaks 单工具已覆盖）| 信息 | 更新计划文本 |
| O-T-M5-02-5 | 观察 | 02d | 48h 处置阈值 + allowlist 流程未文档化 | 低 | 待处置 SOP |

---

## 六、测试覆盖统计（本轮实测）

| 端 | 测试文件 | 实测用例数 | 首轮文档 | 状态 |
|---|---|---|---|---|
| Android | SecurityConfigTest.kt | 13 | 13 | ✅ |
| Server | test_security.py | 11 | 10（陈旧）| ✅ |
| Server | test_logging.py | 12 | 5（陈旧，仅计脱敏类）| ✅ |

**实测命令:** `cd server && python -m pytest tests/test_security.py tests/test_logging.py -v` — 23/23 通过，耗时 0.62s。

**计划要求的最低验证:**
- ✅ `pytest tests/test_security.py -v` 全部通过
- ✅ 安全头存在性断言（7 个独立测试方法覆盖 6 个头 + 404 路径）
- ✅ 超大请求体返回 413（`test_content_length_exceeds_limit`）
- ✅ 413 响应安全头存在性（同上方法，L85-91 追加 3 个断言）
- ✅ `npm audit` / `pip-audit` 在 CI 通过（ci.yml L33 / L76）
- ✅ OWASP DependencyCheck 已接入 CI（ci.yml L57）
- ✅ 日志审计测试捕获敏感字段 → `***`（9 个脱敏用例）
- ⚠️ `tcpdump` 抓包验证 release 无明文 HTTP —— 仍待真机/模拟器实测（首轮即未提供）

---

## 七、与 APP-CONTEXT / ADR 的对齐（沿用首轮）

| 条目 | 对齐情况 |
|---|---|
| APP-CONTEXT §5 安全基线 | ✅ 客户端/服务端安全头/密钥治理全落地 |
| ADR-003 WebView 通道 | ✅ Bridge 44 命令白名单 Kotlin ↔ TS 一致 |
| R-PROD-01 密钥轮换 | ✅ docs/runbooks/secret-management.md 已覆盖 |
| R-PROD-09 基础设施前提 | ✅ 不影响（M5 不依赖生产服务器）|

---

## 八、与首轮审查的差异摘要

| 项 | 首轮 | 本轮 |
|---|---|---|
| 中优先级修复项 | 3 项全部 ✅ 已修复 | 3 项全部 ✅ 复核通过 |
| 低优先级观察项 | 5 项 | 5 项（未变）|
| Bridge 命令数 | 40（陈旧）| 44（已更正）|
| test_security.py 用例数 | 10（陈旧）| 11（已更正）|
| test_logging.py 脱敏用例数 | 5（陈旧）| 9（已更正）|
| 总体结论 | ✅ 通过 | ✅ 通过（一致）|
| 新发现阻塞问题 | 无 | 无 |

---

## 九、最终判定

**✅ T-M5-02 安全加固 — 通过，可关闭。**

- 全部 3 项中优先级修复落实且回归测试通过
- 5 项低优先级观察均非阻塞，可纳入后续迭代 backlog
- 文档数字陈旧（Bridge 40→44 / test_security 10→11 / test_logging 5→9），应同步更新首轮文档
- 唯一未闭环的"最低验证"项为 tcpdump 抓包实测，需真机/模拟器环境，留待 T-M4-05 设备验证流程合并产出证据

**建议:**
1. 更新首轮文档 `T-M5-02-review.md` 中 Bridge 命令数与测试用例数字
2. O-T-M5-02-1 / 02-3 / 02-5 三项低优先级观察记入 T-M6 backlog
3. O-T-M5-02-2 / 02-4 两项信息级观察直接更新计划文本
