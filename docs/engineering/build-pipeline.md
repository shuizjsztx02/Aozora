# Aozora · 构建与发布基线

当前 M1 本地门禁已验证 React 构建、Android 编译及 JVM 测试；设备验收和远端 CI 尚待验证，不代表可分发版本。

T-M1-03 起，Android `preBuild` 必须先执行 `app` 内的 `npm run build`，再把 `app/dist` 同步到 Gradle 生成的 assets 目录。APK 只加载这份 Vite 产物；源码目录中的静态占位页不作为构建输入。CI 分别执行前端门禁与 Android JVM 测试/Debug APK 编译。

发布任务必须在后续 M6 中建立，并要求：依赖锁定、CI 验证、外部签名、密钥扫描、可审计构建产物、数据库迁移审批与回滚运行手册。

在任何发布前执行 `tools/validate-baseline.ps1` 及当前任务要求的完整测试集。

## T-M1-01/03：本机长期开发环境（2026-09-04）

- 当前 Windows 用户的 JDK 固定安装在 `%USERPROFILE%\.jdks\temurin-17.0.20.1`，版本 Temurin 17.0.20.1+1；用户级 `JAVA_HOME` 指向该目录。
- Android SDK 固定安装在 `%LOCALAPPDATA%\Android\Sdk`；用户级 `ANDROID_HOME` 和兼容变量 `ANDROID_SDK_ROOT` 均指向它。已具备 Android 34、Build Tools 34.0.0、Platform Tools 37.0.1 和命令行工具。
- 用户 PATH 追加 JDK `bin`、SDK `platform-tools`、`cmdline-tools\latest\bin`，保留原有其他条目。Node/npm 使用既有 `D:\NodeJS\NodeJS` 安装；Gradle 使用仓库锁定的 8.9 Wrapper 和用户目录 `.gradle` 缓存，不依赖临时 Gradle 安装。
- 本机 `local.properties` 指向固定 SDK，已被 Git 忽略；不要提交机器专属路径。Android Studio 的 SDK Location 应选择上述目录，Gradle JDK 选择上述 JDK 17；本轮未修改 IDE 私有配置。
- 修改环境变量后完全退出并重启 IDE/Codex/终端。旧进程不会自动继承新变量；Codex 沙箱中的用户环境可能隔离，Android 构建仍需按工具审批访问真实用户工具链及缓存，不能以恢复临时路径作为替代。
- 机器 PATH 原有另一份固定安装的 adb（`D:\AndroidPlaformTools\platform-tools-latest-windows\platform-tools`，37.0.0），优先于用户 PATH；未擅自删除或修改机器范围配置。为确保版本一致，设备命令使用 `& "$env:ANDROID_HOME\platform-tools\adb.exe" devices -l`，Gradle 使用 SDK 内版本。
- 安装/升级依赖须通过版本锁和构建验证；npm 用 `npm ci`，Gradle 用 Wrapper。现有 SDK XML v3/v4 警告不阻塞构建；升级 AGP/工具链应单独验证，不能为了消除警告随意升级项目。
- 回退：恢复此前用户环境变量/PATH 并移除本机 `local.properties` 即可切换配置；原临时副本保留，本轮未删除目录或数据。后续清理临时目录不再影响当前工具链。
