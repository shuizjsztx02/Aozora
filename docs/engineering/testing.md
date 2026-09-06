 # 测试策略
 
 **状态：** 已定稿 | **上游依据：** `docs/architecture/overview.md`
 
 ## 测试分层
 | 层 | 内容 | 工具 | 门禁 |
 |---|---|---|---|
 | 规则 NLU | 解析用例集（现 32 例，bug 修复同步补例） | `node tools/test_nlu.js` | 全绿才可发版 |
 | 单元测试 | parser / store 归约 / 统计聚合 / migrate / 金额与日期工具 | vitest | 全绿 |
 | Kotlin JVM 单元 | 同步 payload 映射、错误分级与批量策略、桥协议 | JUnit4（`:app:testDebugUnitTest`） | 全绿 |
 | Android 同步与持久化 | in-memory Room + MockWebServer 同步引擎、WorkManager worker（TestListenableWorkerBuilder）、Room migration（MigrationTestHelper）、认证轮换 | androidTest（CI 仅编译，设备执行） | 全绿；设备不稳定用例按 R-M1-44 惯例 `@Ignore` 并附手工 adb 协议 |
 | 对账不变量 fuzz | 随机操作序列后断言流水求和 ≡ 各统计页数字 ≡ 导出 CSV 汇总 | vitest（伪随机种子固定） | 全绿（一票否决项） |
 | 异常注入 | 断网/超时/非法 JSON/关闭 AI 四态演练 | 手工 checklist | 行为一致 |
 | 真机矩阵 | Android 8/12/15 + 深色模式 + 小屏字体放大 1.3× | 手工走查 | 每次发版前 |
 | 发布门禁 | crash-free ≥99.5%、性能基线达标 | 崩溃监控 + 实测 | 达标 |
 
 ## 验收五原则
 1. **可验证优先**：每条验收标准必须是可执行的命令、操作步骤或数值阈值
 2. **真机为准**：模拟器/浏览器通过不算数
 3. **数据正确性一票否决**：对账不变量 fuzz 必须全绿
 4. **异常路径必须演过**：AI 相关改动必须在四种异常态下演示
 5. **一个 bug 一条守护**：每个修复附带一条自动化回归用例
 
 ## 性能基线
 | 指标 | 目标 | 测法 |
 |---|---|---|
 | 冷启动到可交互（主力机 Android 12） | ≤2s | `adb shell am start -W` 多次取中位数 |
 | 本地记一笔保存 p95 | ≤300ms | 日志打点 |
 | LLM 响应 | t+12s 出慢速提示，t+25s 强制降级 | 断网/限速演练计时 |
 | 语音首字识别延迟 | <1s（安静环境） | 实测录音 |
 | 包体 | 不回退超过上一版 10% | 归档 APK 体积对比 |
