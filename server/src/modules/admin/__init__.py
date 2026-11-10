"""T-M8-04：运营可观测后台（M8）。

职责边界（`docs/architecture/admin-console.md` §5–§7）：

- **只读**：本模块的全部端点只读业务表；唯一的写是登录审计、``reveal`` 审计
  与登录时的 ``last_login_at``。写操作（暂停 / 配额 / 强制登出）属于 T-M8-05。
- **独立鉴权**：管理员账号与独立 JWT（``aud=aozora-admin``），不复用 C 端的
  secret、audience 与限流器。
- **列表不返回事件正文**：正文只在 ``/admin/conversations/{id}/events``，
  且默认脱敏。
"""
