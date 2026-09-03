# Aozora · 架构总览

生产客户端采用 Kotlin WebView 壳与 React UI。Kotlin 负责 Room、本地离线队列、Keystore、网络和受限原生能力；React 只负责展示、交互和确认状态。

登录用户数据以服务端 PostgreSQL 为权威。客户端 Room 是本地副本和离线工作区；服务端 API 负责鉴权、事务、幂等、审计和同步，AI Gateway 仅输出经过校验的动作提案。

T-M0 仅包含受限 WebView 与 React 空壳。目标组件边界、数据不变量和后续任务以 `docs/APP-CONTEXT.md` 为准。
