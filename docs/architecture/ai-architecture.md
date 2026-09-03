# Aozora · AI 架构

本地规则 NLU 提供离线兜底，服务端 AI Gateway 提供在线理解。模型密钥只存在服务端密钥管理环境；客户端不得直接调用模型提供商。

AI 只返回通过 JSON Schema 校验的 ActionProposal。金额、日期、权限和写库始终由确定性业务代码负责，低置信结果必须先经用户确认。
