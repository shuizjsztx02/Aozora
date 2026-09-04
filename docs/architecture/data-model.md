# Aozora · 数据模型约束

- 金额以正整数 `amountCents` 存储；收支方向由 `type` 表达。
- 登录用户的服务端数据是权威；Room 仅保存本地副本和待同步操作。
- 每个写操作携带 `mutationId`，服务端必须幂等处理。
- 可修改实体带 `version`，冲突必须显式返回，禁止静默覆盖。
- 交易同时保存 UTC `occurredAt` 和用户业务日期 `bizDate`。
- 统计口径：`MonthlyStats.balance = max(0, totalIncome − totalExpense)`。`MoneyCents` 禁止负值，赤字（支出 > 收入）不存入 balance，由展示层用 `totalIncome/totalExpense` 推导。客户端统计、服务端统计与导出必须使用同一口径，保证 APP-CONTEXT §5.2 对账不变量（客户端明细合计 = 客户端统计 = 服务端统计 = 导出文件合计）。
- 空预算语义：`Budget.categoryId` 与 `Category.bookId` 以显式 `null`（JSON 标准表达）或字段缺省表示“整体预算 / 系统分类”，客户端类型须接受 `string | null | undefined` 三种形态。

完整实体和同步规则以 `docs/APP-CONTEXT.md` 第 5 节为准。
