# T-M2-01 · Aozora Production API

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
├── main.py           # FastAPI 应用入口
└── ...               # 业务模块（后续任务）
server/tests/         # pytest 测试
```

## 环境变量

| 变量 | 必需 | 默认 | 说明 |
|---|---|---|---|
| `ENV` | 否 | `development` | `development` / `staging` / `production` |
| `DATABASE_URL` | 是 | — | PostgreSQL async 连接串 |
| `SECRET_KEY` | 是（production） | `dev-secret-do-not-use-in-production` | 签名密钥 |
| `REDIS_URL` | 否 | `None` | Redis 连接（首发非必需） |
| `API_PREFIX` | 否 | `/api/v1` | API 路由前缀 |
| `CORS_ORIGINS` | 否 | `*` | CORS 允许来源，逗号分隔 |
| `LOG_LEVEL` | 否 | `INFO` | 日志级别 |

## 端点

| 路径 | 说明 |
|---|---|
| `GET /health` | Liveness — 进程存活 |
| `GET /health/ready` | Readiness — 含数据库连通性检查 |
| `GET /docs` | Swagger UI（OpenAPI 3.1） |
| `GET /redoc` | ReDoc |

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
