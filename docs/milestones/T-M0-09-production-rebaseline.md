# T-M0-09 · Production Rebaseline

**状态：** 已完成（2026-09-03）
**目标：** 删除原型工作区实现，建立首个安全、可长期迭代的 Aozora Git 基线。
**权威来源：** `docs/APP-CONTEXT.md` 与已批准的 T-M0-09 设计。

## 保留内容

- 已完成冲突清理的产品、架构、设计和工程文档。
- 重建后的 Android/React 最小壳与根 Gradle 配置。

## 已删除内容

- 历史 APK、签名材料、原型 Android/React 源码、Vosk 资产、打包 WebView 产物、旧文档、旧实现和原型采集文件。

## 完成证据

- `powershell -ExecutionPolicy Bypass -File tools/validate-baseline.ps1` 通过。
- 在 `app/` 中执行 `npm run lint`、`npm run build` 通过。
- 使用 JDK 17、Android SDK 34 与已提交的 Gradle 8.9 Wrapper 执行 `./gradlew.bat :app:assembleDebug` 通过。
- 初始安全基线提交：`71e7b2c chore(T-M0-09): establish Aozora production baseline`。
- 可复现 Android 构建补丁：`db1f1cf build(T-M0-09): add Gradle wrapper`。
- GitHub 私有远端：`origin` → `https://github.com/shuizjsztx02/Aozora.git`，`main` 已推送并跟踪 `origin/main`。

## 后续约束

本任务不意味着生产功能、密钥轮换或基础设施已经交付。后续执行统一遵循 [生产执行规划](production-execution-plan.md)。