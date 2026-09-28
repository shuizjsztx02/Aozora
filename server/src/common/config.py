"""T-M2-01: Environment configuration with startup validation."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Default value for ``secret_key`` — must be overridden in production.
_DEV_SECRET_KEY = "dev-secret-do-not-use-in-production"

# Production CORS: only `https://[<subdomain>.]aozora.app` is permitted.
# Rejects bare `*`, third-party origins, and http:// schemes at startup so
# a misconfigured env-var cannot silently widen the origin allow-list.
_PROD_CORS_ORIGIN_RE = re.compile(r"^https://([a-z0-9-]+\.)?aozora\.app$")


class Settings(BaseSettings):
    """Validated application settings.

    Each field reads from the environment variable matching its alias
    (case-insensitive via pydantic-settings).  Missing required fields
    cause a ``ValidationError`` at load time, which prevents startup.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- runtime ---
    # CR-02 fix: Literal constraint prevents typos from silently bypassing
    # production safety checks (secret_key/CORS/dev_auth validation).
    env: Literal["production", "development", "test", "testing", "staging"] = Field(
        default="development",
        validation_alias=AliasChoices("ENV", "env"),
    )

    # --- database (REQUIRED — no default) ---
    database_url: str = Field(
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )

    # --- security ---
    secret_key: str = Field(
        default=_DEV_SECRET_KEY,
        validation_alias=AliasChoices("SECRET_KEY", "secret_key"),
    )

    # --- jwt ---
    jwt_algorithm: Literal["HS256", "RS256", "ES256"] = Field(
        default="HS256",
        validation_alias=AliasChoices("JWT_ALGORITHM", "jwt_algorithm"),
    )
    access_token_expire_minutes: int = Field(
        default=15,
        gt=0,
        validation_alias=AliasChoices("ACCESS_TOKEN_EXPIRE_MINUTES", "access_token_expire_minutes"),
    )
    refresh_token_expire_days: int = Field(
        default=30,
        gt=0,
        validation_alias=AliasChoices("REFRESH_TOKEN_EXPIRE_DAYS", "refresh_token_expire_days"),
    )

    # --- auth ---
    dev_auth_code: str = Field(
        default="000000",
        validation_alias=AliasChoices("DEV_AUTH_CODE", "dev_auth_code"),
    )

    # --- redis (optional for first release) ---
    redis_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("REDIS_URL", "redis_url"),
    )

    # --- T-M5-02b: request-body size limit (non-upload endpoints) ---
    max_request_size: int = Field(
        default=10 * 1024 * 1024,
        validation_alias=AliasChoices("MAX_REQUEST_SIZE", "max_request_size"),
    )

    # --- api ---
    api_prefix: str = Field(
        default="/api/v1",
        validation_alias=AliasChoices("API_PREFIX", "api_prefix"),
    )

    # --- cors ---
    cors_origins: str = Field(
        default="*",
        validation_alias=AliasChoices("CORS_ORIGINS", "cors_origins"),
    )

    # --- logging ---
    log_level: str = Field(
        default="INFO",
        validation_alias=AliasChoices("LOG_LEVEL", "log_level"),
    )

    # --- T-M5-05a: OpenTelemetry ---
    otel_endpoint: str = Field(
        default="http://otel-collector:4317",
        validation_alias=AliasChoices("OTEL_ENDPOINT", "otel_endpoint"),
    )
    enable_telemetry: bool = Field(
        default=True,
        validation_alias=AliasChoices("ENABLE_TELEMETRY", "enable_telemetry"),
    )
    # T-M5-05b follow-up: in-process daily D1/D7 retention job. Default off —
    # enabled explicitly in docker-compose.staging.yml (and production when
    # the funnel panels are wired). Off means the gauge simply keeps "No
    # data" (the dashboard label documents this).
    retention_job_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("RETENTION_JOB_ENABLED", "retention_job_enabled"),
    )

    # --- exports ---
    # N-05: export directory. Defaults to a /tmp sub-directory (ephemeral
    # on most OSes); override via EXPORT_DIR for persistent deployments.
    # Files are auto-deleted when their export expires (N-06).
    export_dir: str = Field(
        default="",
        validation_alias=AliasChoices("EXPORT_DIR", "export_dir"),
    )

    # --- attachments (T-M4-04c) ---
    # D-EXEC-05 (object storage) is pending external confirmation;
    # until then, uploads land in a local temp directory that mirrors
    # the export-dir pattern (ephemeral, reclaimed on startup).
    # Override via UPLOAD_DIR for persistent deployments.
    upload_dir: str = Field(
        default="",
        validation_alias=AliasChoices("UPLOAD_DIR", "upload_dir"),
    )

    # --- ai gateway (T-M4-01) ---
    # D-EXEC-04 (AI account/quota/limits/rotation) is pending external
    # confirmation; defaults below are conservative placeholders.
    # A missing key does NOT block startup in dev/staging/test — the
    # gateway degrades to 503 at runtime so those environments run
    # without provider access. In production the key IS required
    # (T-M4-01a acceptance: refuse startup — see _check_production_safety).
    ai_provider_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AI_PROVIDER_API_KEY", "ai_provider_api_key"),
    )
    ai_provider_model: str = Field(
        default="qwen3.7-flash",
        validation_alias=AliasChoices("AI_PROVIDER_MODEL", "ai_provider_model"),
    )
    ai_provider_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias=AliasChoices("AI_PROVIDER_BASE_URL", "ai_provider_base_url"),
    )
    # Per-attempt timeout. Contract (APP-CONTEXT §6.4): the client cancels
    # at 25 s, so 2 × timeout + overhead must stay below that budget.
    # R-M4-42: enforce le=12 (leaves ~1 s overhead for 2 × 12 + 1 = 25 s);
    # without the upper bound an ops misconfiguration silently breaks the
    # degradation timing contract with the client.
    ai_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=12.0,
        validation_alias=AliasChoices("AI_TIMEOUT_SECONDS", "ai_timeout_seconds"),
    )
    # Per-user burst protection — applies to ALL users (guests and active);
    # it guards the provider against runaway loops, not a product quota.
    ai_rate_limit_per_minute: int = Field(
        default=10,
        gt=0,
        validation_alias=AliasChoices("AI_RATE_LIMIT_PER_MINUTE", "ai_rate_limit_per_minute"),
    )
    # Daily quota for GUEST users only; ACTIVE users have no daily cap
    # (product decision 2026-09-06). Counted per calendar day in the
    # user's own timezone (User.timezone).
    # 联测临时提额（2026-09-11，T15 真机+模拟器回归多句连测）原值 20，
    # **T-M7 收口时恢复**（2026-09-20）——提额是给人工联测用的，留在默认值里
    # 等于把「游客一天 20 次」这个产品决策悄悄作废。
    # 两个部署实例由 compose 显式接管：api-test 走 ``TEST_AI_QUOTA``（默认
    # 100000，继续方便连测），api-prod 走 ``PROD_AI_QUOTA``（默认 20）。
    # 本机真机联调同理——在未跟踪的 ``server/.env`` 里覆盖，不改代码。
    ai_daily_quota_guest: int = Field(
        default=20,
        gt=0,
        validation_alias=AliasChoices("AI_DAILY_QUOTA_GUEST", "ai_daily_quota_guest"),
    )
    # T-M4-04e: vision model for image receipt parsing. Separate from the
    # text model because VL models have different cost/latency profiles.
    ai_vision_model: str = Field(
        default="qwen-vl-max",
        validation_alias=AliasChoices("AI_VISION_MODEL", "ai_vision_model"),
    )
    # Maximum image bytes to send to the vision model (4 MB). Larger images
    # are downscaled before encoding. 0 = skip image parsing entirely.
    ai_image_max_bytes: int = Field(
        default=4 * 1024 * 1024,
        ge=0,
        validation_alias=AliasChoices("AI_IMAGE_MAX_BYTES", "ai_image_max_bytes"),
    )
    # Cost accounting: price per 1M tokens in micro-CNY (1e-6 元).
    # 0 = not priced yet (until D-EXEC-04 confirms real rates).
    ai_price_per_1m_tokens: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("AI_PRICE_PER_1M_TOKENS", "ai_price_per_1m_tokens"),
    )

    # --- AI agent 路径 provider (T-M7-02b, ADR-007) ---
    # The agent path (multi-step loop) runs on DeepSeek; the ``ai_provider_*``
    # block above keeps serving the fast path (DashScope). Both coexist and
    # neither displaces the other. Keys come from deployment secret storage
    # (D-EXEC-04) — never from the repository.
    # Unlike ``ai_provider_api_key``, a missing key does NOT block production
    # startup: the agent path ships behind a default-off rollout switch, so an
    # unconfigured deployment simply keeps serving the fast path.
    ai_agent_provider_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "AI_AGENT_PROVIDER_API_KEY", "ai_agent_provider_api_key",
        ),
    )
    ai_agent_provider_model: str = Field(
        default="deepseek-flash",
        validation_alias=AliasChoices(
            "AI_AGENT_PROVIDER_MODEL", "ai_agent_provider_model",
        ),
    )
    ai_agent_provider_base_url: str = Field(
        default="https://api.deepseek.com",
        validation_alias=AliasChoices(
            "AI_AGENT_PROVIDER_BASE_URL", "ai_agent_provider_base_url",
        ),
    )

    # --- AI agent 会话窗口与配额 (T-M7-02f) ---
    # 上下文窗口「最近 N 轮」（架构 §6.3；§12.2 未决 #2：初始 8，需实测调优）。
    # 「轮」= 一次用户输入跑完整个 loop，不是一次模型请求——工具任务的
    # 第 2 步起恒为深档（02d 实测），按请求数计窗口会让上下文忽长忽短。
    ai_agent_history_turns: int = Field(
        default=8,
        gt=0,
        validation_alias=AliasChoices(
            "AI_AGENT_HISTORY_TURNS", "ai_agent_history_turns",
        ),
    )
    # 与轮数并行的字符双限（架构 §6.4「按 N 轮 + 字符双限」）。工具结果会留在
    # 历史里被每个后续 Step 重发，轮数够小也可能撑爆单步预算。
    ai_agent_history_chars: int = Field(
        default=8000,
        gt=0,
        validation_alias=AliasChoices(
            "AI_AGENT_HISTORY_CHARS", "ai_agent_history_chars",
        ),
    )
    # 摘要的字符上限（架构 §6.4「摘要 800 字符」）。**超限的摘要是整条退回**，
    # 不截断——见 `conversation.ConversationService.summarize_overflow`。它是
    # 窗口之外那部分历史的**唯一**载具，与 `ai_agent_history_chars`（窗口内
    # 原文的预算）量的是两件事，不是同一个上限的两种写法。
    ai_agent_summary_chars: int = Field(
        default=800,
        gt=0,
        validation_alias=AliasChoices(
            "AI_AGENT_SUMMARY_CHARS", "ai_agent_summary_chars",
        ),
    )
    # --- AI 会话日志保留期 (T-M7-04d) ---
    # 事件正文的保留窗口（天）。security.md 的有条件例外要求「保留期对齐
    # D-PROD-08」——D-PROD-08 的 7 天是**账号删除的宽限期**，不是内容保留期；
    # 对齐的是保证形态（有界、到点物理删除），数值取 30 天：覆盖会话被重新拾起
    # 的现实窗口，同时把服务端持有的正文量钉死在「活跃用户 × 最近 30 天」。
    # 与 §6.3 的窗口 N、会话级双限同口径——**初始值**，真实使用分布出来后校准。
    ai_conversation_retention_days: int = Field(
        default=30,
        gt=0,
        validation_alias=AliasChoices(
            "AI_CONVERSATION_RETENTION_DAYS", "ai_conversation_retention_days",
        ),
    )
    # 保留期任务的开关。**默认关闭**，由部署侧显式开启（同 RETENTION_JOB_ENABLED
    # 的口径：`docker-compose.staging.yml` / `docker-compose.prod.yml` 已置 true）。
    # 关闭意味着 security.md 的第一条约束不成立——那是部署缺陷，不是配置选项；
    # 默认值取 false 只是因为测试进程不该起后台任务（同 RETENTION_JOB_ENABLED 的
    # 理由），**不**意味着生产可以不开。
    ai_conversation_retention_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "AI_CONVERSATION_RETENTION_ENABLED", "ai_conversation_retention_enabled",
        ),
    )

    # --- AI agent SSE 端点 (T-M7-02g) ---
    # 灰度开关（架构 §11.3 的双层开关：本项是服务端层，客户端层在 02i 的
    # ``submitInput`` 上）。**默认关闭**——关闭时 ``POST /ai/agent`` 按 404 处理，
    # 未升级的客户端与快路径完全不受影响。
    ai_agent_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("AI_AGENT_ENABLED", "ai_agent_enabled"),
    )
    # SSE 空闲心跳（架构 §8.4，R-PROD-22 第 2 项）：服务器未设
    # ``proxy_read_timeout``，用 nginx 默认 60 s——静默 60 s 即被断开。心跳是注释
    # 帧（``: ping``），不占事件语义。**必须显著小于 60 s**，故上限钉在 60。
    ai_agent_heartbeat_seconds: float = Field(
        default=15.0,
        gt=0,
        le=60.0,
        validation_alias=AliasChoices(
            "AI_AGENT_HEARTBEAT_SECONDS", "ai_agent_heartbeat_seconds",
        ),
    )
    # agent 路径的**单步** provider 超时（架构 §8.3：计时单位由「整个请求」改为
    # 「单步」）。不复用 ``ai_timeout_seconds``——那一项的上限 12 s 来自快路径的
    # 「2 × timeout + 开销 < 客户端 25 s 取消」契约，而 agent 路径的客户端取消是
    # **按步**计的（02h 为该长连接单独配置超时）。取 20 s：低于客户端单步 25 s
    # 取消（服务端先失败，才能把 ``error`` 帧发出去），高于 02d 实测的深档单步
    # 5–7 s。
    #
    # **自 02g-2 起，这一项计的是「静默」而不是「总时长」**：流式请求下 httpx 的
    # read timeout 语义即「两个数据块之间最多等多久」，一个在稳定输出的长回答因此
    # 不会被掐掉（那正是我们要保留的形态），真正被判死的只有「卡住不动」。客户端
    # 侧（02h）按同一口径用 OkHttp 的 read timeout，不用 ``callTimeout``。
    ai_agent_step_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        validation_alias=AliasChoices(
            "AI_AGENT_STEP_TIMEOUT_SECONDS", "ai_agent_step_timeout_seconds",
        ),
    )

    # --- 设备能力通道 (T-M7-05e) ---
    # 通道开关（ADR-010 决策 7）。**默认关闭**——开启前 loop 不注入设备工具
    # （模型看不到它们，行为与 P1 逐字相同）。设备能力要在用户设备上执行，得有
    # 客户端执行器（05f）；在那之前让模型调用一个没人应答的能力，只会让回合
    # 白等一个超时窗。与 ``ai_agent_enabled`` 同一取向。
    ai_device_channel_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "AI_DEVICE_CHANNEL_ENABLED", "ai_device_channel_enabled",
        ),
    )
    # 一次**需结果**设备请求的挂起上限（秒）。客户端执行的是人的动作（系统权限
    # 对话框、选图），所以它比 ``ai_agent_step_timeout_seconds`` 宽松得多：
    # 挂起期间流上有心跳（``ai_agent_heartbeat_seconds``），连接不会因为这个时长
    # 被掐断。到期仍未回传即收敛成一条「没等到结果」的失败结果回喂模型
    # （ADR-010 决策 4），回合照常往下走。
    ai_device_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        validation_alias=AliasChoices(
            "AI_DEVICE_TIMEOUT_SECONDS", "ai_device_timeout_seconds",
        ),
    )

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _check_production_safety(self) -> Settings:
        """Block obviously-insecure defaults when running in production."""
        if self.env != "production":
            return self
        if self.secret_key == _DEV_SECRET_KEY:
            raise ValueError(
                "SECRET_KEY must be set to a unique, unpredictable value in production"
            )
        if self.dev_auth_code:
            raise ValueError(
                "DEV_AUTH_CODE must be empty in production; SMS provider not yet integrated"
            )
        if self.cors_origins.strip() == "*":
            raise ValueError(
                "CORS_ORIGINS must be an explicit allow-list in production"
            )
        bad_cors = [
            o
            for o in (x.strip() for x in self.cors_origins.split(","))
            if o and not _PROD_CORS_ORIGIN_RE.match(o)
        ]
        if bad_cors:
            raise ValueError(
                "CORS_ORIGINS in production must match "
                f"https://[<subdomain>.]aozora.app; got: {bad_cors}"
            )
        if not self.ai_provider_api_key:
            raise ValueError(
                "AI_PROVIDER_API_KEY must be set in production; the 503 "
                "degradation fallback is only for dev/staging"
            )
        return self


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_settings: Settings | None = None


def load_config() -> Settings:
    """Load and validate configuration from environment / .env file."""
    return Settings()


def get_config() -> Settings:
    """Return cached settings, loading on first call."""
    global _settings
    if _settings is None:
        _settings = load_config()
    return _settings


def set_config(settings: Settings) -> None:
    """Override settings (used in tests)."""
    global _settings
    _settings = settings


def reset_config() -> None:
    """Clear cached settings (used in tests)."""
    global _settings
    _settings = None
