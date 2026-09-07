# 密钥管理 Runbook

> T-M5-02c: 密钥注入路径、轮换流程与环境变量清单。

## 环境变量清单

以下密钥**仅通过环境变量或 secret manager 注入**，配置文件中持占位符，不得提交到仓库。

### 服务端 (`server/.env`)

| 变量 | 用途 | 注入方式 | 生产要求 |
|---|---|---|---|
| `SECRET_KEY` | JWT 签名密钥 | 环境变量 / CI Secret | 必须设置；默认值 `dev-secret-do-not-use-in-production` 在生产启动时拒绝 |
| `AI_PROVIDER_API_KEY` | AI 模型 API 密钥（DashScope） | 环境变量 / CI Secret | 生产必须设置；dev/staging 缺失降级为 503 |
| `DATABASE_URL` | PostgreSQL 连接字符串 | 环境变量 | 必须设置；无默认值 |
| `DEV_AUTH_CODE` | 开发环境验证码 | 环境变量 | 生产必须为空（`""`） |
| `CORS_ORIGINS` | CORS 白名单 | 环境变量 | 生产必须为逗号分隔的域名列表；不允许 `*` |

### 客户端 (`app/build.gradle.kts`)

| 变量 | 用途 | 注入方式 | 备注 |
|---|---|---|---|
| `AOZORA_API_BASE` | 生产 API 基址 | Gradle property (`-PAOZORA_API_BASE=...`) | Release 构建注入；缺省回退到 `https://api.example.invalid` |
| `KEYSTORE_PATH` | 签名 keystore 路径 | 环境变量 | 由 CI secret 注入；keystore 不进仓库 |
| `KEYSTORE_PASSWORD` | Keystore 密码 | 环境变量 | 同上 |
| `KEY_ALIAS` | 签名密钥别名 | 环境变量 | 同上 |
| `KEY_PASSWORD` | 密钥密码 | 环境变量 | 同上 |

## 注入路径

### 本地开发

```bash
# 1. 复制 .env 模板
cp server/.env.example server/.env
# 2. 编辑 server/.env 填入开发环境值（不进 git）
# 3. 启动
cd server && uvicorn src.main:app --reload
```

### CI / CD

密钥通过 **GitHub Actions Secrets** 注入到 workflow 环境变量：

```yaml
env:
  SECRET_KEY: ${{ secrets.SECRET_KEY }}
  AI_PROVIDER_API_KEY: ${{ secrets.AI_PROVIDER_API_KEY }}
  DATABASE_URL: ${{ secrets.DATABASE_URL }}
```

密钥**不进入 Docker 镜像层**——由 `docker-compose` 运行时注入。

### 生产环境

1. 通过 secret manager 或 `.env` 文件注入（不进版本控制）
2. `docker-compose.prod.yml` 引用 `${SECRET_KEY}` 等变量
3. 启动时 `_check_production_safety` 验证器强制检查：
   - `SECRET_KEY` 不为默认值
   - `DEV_AUTH_CODE` 为空
   - `CORS_ORIGINS` 不为 `*`
   - `AI_PROVIDER_API_KEY` 已设置

## 轮换流程

### AI API 密钥轮换

1. 在 AI 提供商控制台创建新 API 密钥
2. 更新 GitHub Actions Secrets (`Settings → Secrets → AI_PROVIDER_API_KEY`)
3. 更新生产环境 `.env` 中的 `AI_PROVIDER_API_KEY`
4. 重启 API 服务 (`docker compose restart api`)
5. 在提供商控制台**撤销旧密钥**
6. 验证：`curl -I https://api.aozora.app/health` 返回 200

### JWT 签名密钥轮换

> ⚠️ 轮换 `SECRET_KEY` 会导致所有现有 access token 和 refresh token 立即失效，用户需重新登录。

1. 在低流量时段执行（如周二发布窗口内）
2. 更新 secret manager / `.env` / GitHub Secrets
3. 重启 API 服务
4. 通知用户需要重新登录

## 密钥扫描

### CI 自动扫描

`.github/workflows/secret-scan.yml` 使用 gitleaks：
- **每次 PR / push**: 扫描增量 diff
- **每周一 03:00 UTC**: 全历史扫描

### 产物扫描

`tools/scan-secrets.sh` 扫描三类产物：
```bash
# APK/AAB
tools/scan-secrets.sh app/build/outputs/bundle/release/app-release.aab

# Web dist
tools/scan-secrets.sh "" app/dist

# 服务端镜像（需 docker build 后运行）
tools/scan-secrets.sh "" "" aozora-api:latest
```

### 手动检查

```bash
# Git 历史中搜索疑似密钥
git log --all --diff-filter=A --name-only | xargs grep -lE 'sk-[a-zA-Z0-9]{20,}'

# 仓库内搜索
grep -rE 'sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{36}' --include='*.py' --include='*.ts' --include='*.kt' .
```

## 意外泄露处置

如果密钥被提交到 Git 历史：

1. **立即轮换**该密钥（旧密钥不可信）
2. 使用 `git filter-branch` 或 BFG Repo Cleaner 从历史中删除
3. `git push --force` 更新远程
4. 通知所有协作者重新 clone
5. 记录事件到事故复盘

> ⚠️ 即使从 Git 历史中删除，如果仓库已被 fork 或 clone，泄露的密钥仍可能已被他人获取。**轮换是唯一可靠的补救措施**。
