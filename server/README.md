# Aozora · Production API

## 快速开始

```bash
cd server
cp .env.example .env
# 编辑 .env 填入 DATABASE_URL 和 SECRET_KEY
pip install -e ".[dev]"
pytest
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

## 目录结构

```text
server/src/
├── common/           # 配置、日志、请求 ID、错误处理、数据库
├── models/           # SQLAlchemy 模型（11 实体）
├── migrations/       # Alembic 迁移
├── modules/
│   └── auth/         # 认证与会话（游客、手机登录、token rotation）
├── main.py           # FastAPI 应用入口
server/tests/         # pytest 测试（85 项）
```

## 环境变量

| 变量 | 必需 | 默认 | 说明 |
|---|---|---|---|
| `ENV` | 否 | `development` | `development` / `staging` / `production` |
| `DATABASE_URL` | 是 | — | PostgreSQL async 连接串 |
| `SECRET_KEY` | **production 必填** | `dev-secret-do-not-use-in-production` | 签名密钥；production 启动必须覆盖，否则 `ValidationError` 拒绝启动 |
| `DEV_AUTH_CODE` | production 必须为空 | `000000` | 开发固定验证码；production 必须清空（SMS 供应商待接入） |
| `JWT_ALGORITHM` | 否 | `HS256` | 仅接受 `HS256` / `RS256` / `ES256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 否 | `15` | 必须 > 0 |
| `REFRESH_TOKEN_EXPIRE_DAYS` | 否 | `30` | 必须 > 0 |
| `REDIS_URL` | 否 | `None` | Redis 连接（首发非必需） |
| `API_PREFIX` | 否 | `/api/v1` | API 路由前缀 |
| `CORS_ORIGINS` | production 不得为 `*` | `*` | CORS 允许来源，逗号分隔 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |

## 端点

| 路径 | 说明 |
|---|---|
| `GET /api/v1/health` | Liveness — 进程存活 |
| `GET /api/v1/health/ready` | Readiness — 含数据库连通性检查 |
| `POST /api/v1/auth/guest` | 创建游客账号 |
| `POST /api/v1/auth/login` | 手机验证码登录 |
| `POST /api/v1/auth/refresh` | Refresh token rotation |
| `POST /api/v1/auth/logout` | 注销单个设备会话 |
| `GET /docs` | Swagger UI（OpenAPI 3.1） |
| `GET /redoc` | ReDoc |
| `GET /openapi.json` | OpenAPI 规范 |

## 数据库迁移

```bash
# 执行迁移到最新
alembic upgrade head

# 查看当前版本
alembic current

# 查看迁移历史
alembic history

# 生成增量迁移（需要 PostgreSQL 连接）
alembic revision --autogenerate -m "description"
```

## Docker

```bash
docker compose up --build
# 或单独构建镜像
docker build -t aozora-api:dev .
```

## 测试

```bash
pytest -v
```
