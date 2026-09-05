# Aozora · 生产里程碑总览

**当前里程碑：** M3 进行中（T-M3-01 ✅ 已完成，下一项为 T-M3-02）
**当前任务：** T-M3-02 Android 同步引擎
**执行入口：** [生产执行规划](production-execution-plan.md)

| 里程碑 | 目标 | 出口标准 |
|---|---|---|
| M0 | 决策、仓库与质量基线 | 架构和任务树一致，仓库无密钥与原型产物，任务依赖和交接入口有效。 |
| M1 | 客户端领域与本地可靠性 | TypeScript 与测试门禁、整数分、业务日期、ActionProposal、Room 和本地 NLU 完成。 |
| M2 | 服务端权威业务数据 | 鉴权、账本 API、迁移和统计契约完成。 |
| M3 | 离线同步与恢复 | 幂等 mutation、同步队列和恢复测试完成。 |
| M4 | AI 与原生能力 | 受控动作协议、AI Gateway 和平台能力接线完成。 |
| M5 | 安全、质量与内测 | 对账门禁、安全、备份和内测出口完成。 |
| M6 | 生产发布 | 可重复部署、签名发布和上线观察完成。 |

T-M1 全部通过 ✅。T-M2 全部通过 ✅：T-M2-01 服务端骨架、T-M2-02 数据库 Schema 与迁移、T-M2-03 认证与会话、T-M2-04 账本核心 API（Books/Categories/Accounts/Transactions/Budgets CRUD，ownership guard，version 乐观并发，软删除，分页）、T-M2-05 统计与导出（月度/趋势聚合、JSON/CSV 导出、对账不变量，122 项服务端测试全部通过）。SMS 供应商（D-EXEC-03）待外部确认。下一任务为 T-M3-01 同步契约与变更流。完整依赖、外部待办和验收要求以 `docs/APP-CONTEXT.md` 第 9--10 节、生产执行规划与 `docs/review/T-M1-review.md` 为准。
