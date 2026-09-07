package com.aozora.app

import android.app.Application
import androidx.work.Configuration
import com.aozora.app.ai.AiGatewayClient
import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import com.aozora.app.consent.ConsentStore
import com.aozora.app.crashlytics.CrashReportDatabase
import com.aozora.app.crashlytics.CrashReporter
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.export.ExportClient
import com.aozora.app.image.ImageClient
import com.aozora.app.reminder.ReminderScheduler
import com.aozora.app.sync.HttpSyncApi
import com.aozora.app.sync.SyncEngine
import com.aozora.app.voice.VoiceRecognitionService
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * Aozora Application — wires singletons (T-M3-02).
 *
 * 单例拓扑：
 * - [database] / [repository]：Room + Repository。
 * - [tokenStore] / [authClient]：认证层（AES/GCM + OkHttp）。
 * - [syncEngine]：编排 push/pull，依赖 [repository] 与 [syncApi]。
 * - [reminderScheduler]：每日记账提醒调度（T-M4-05a）。
 *
 * 通过 [(AozoraApp) applicationContext] 在 Activity / Worker 中取回。
 */
class AozoraApp : Application(), Configuration.Provider {

    /** Lazily-built Room database singleton. */
    val database: AozoraDatabase by lazy { AozoraDatabase.getInstance(this) }

    /** Repository wrapping the database. */
    val repository: Repository by lazy { Repository(database) }

    /** TokenStore: AES/GCM 加密的令牌持久化。 */
    val tokenStore: TokenStore by lazy { TokenStore(this) }

    /** OkHttp client：同步 + 认证共用，连接池 60s。 */
    private val httpClient: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            // P1-1: 禁止自动跟随重定向，防止恶意/错误配置服务器将含 refresh_token 的 POST body 重发
            .followRedirects(false)
            .build()
    }

    /** AuthClient: token 生命周期。 */
    val authClient: AuthClient by lazy {
        AuthClient(
            baseUrl = BuildConfig.SYNC_BASE_URL,
            tokenStore = tokenStore,
            httpClient = httpClient
        )
    }

    /** SyncApi: HTTP 传输。 */
    private val syncApi: HttpSyncApi by lazy {
        HttpSyncApi(
            baseUrl = BuildConfig.SYNC_BASE_URL,
            httpClient = httpClient,
            authClient = authClient
        )
    }

    /** SyncEngine: push/pull 编排。 */
    val syncEngine: SyncEngine by lazy {
        SyncEngine(
            repository = repository,
            syncApi = syncApi,
            authClient = authClient
        )
    }

    /** T-M4-05a: 提醒调度器。 */
    val reminderScheduler: ReminderScheduler by lazy {
        ReminderScheduler(this)
    }

    /** T-M4-03c: AI Gateway 客户端（ADR-003 通道）。
     *  独立 OkHttpClient 实例，callTimeout = 25 s（LLM 调用预算）。 */
    val aiGatewayClient: AiGatewayClient by lazy {
        val aiHttpClient = OkHttpClient.Builder()
            .callTimeout(25, TimeUnit.SECONDS)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(20, TimeUnit.SECONDS)
            .followRedirects(false)
            .build()
        AiGatewayClient(
            baseUrl = BuildConfig.SYNC_BASE_URL,
            httpClient = aiHttpClient,
            authClient = authClient
        )
    }

    /** T-M4-04d: 图片上传客户端（与 sync 共用连接池，writeTimeout 30s）。 */
    val imageClient: ImageClient by lazy {
        ImageClient(
            baseUrl = BuildConfig.SYNC_BASE_URL,
            httpClient = httpClient,
            authClient = authClient
        )
    }

    /** T-M5-04b: 数据导出客户端（与 sync 共用连接池）。 */
    val exportClient: ExportClient by lazy {
        ExportClient(
            baseUrl = BuildConfig.SYNC_BASE_URL,
            httpClient = httpClient,
            authClient = authClient
        )
    }

    /** T-M5-04c: 账号删除客户端（与 sync 共用连接池）。 */
    val deletionClient: com.aozora.app.deletion.DeletionClient by lazy {
        com.aozora.app.deletion.DeletionClient(
            baseUrl = BuildConfig.SYNC_BASE_URL,
            httpClient = httpClient,
            tokenStore = tokenStore
        )
    }

    /** T-M4-04b: Vosk 离线语音识别服务。
     *  首次使用时需调用 initialize() 加载模型（约 1-2 秒）。 */
    val voiceRecognitionService: VoiceRecognitionService by lazy {
        VoiceRecognitionService(this)
    }

    /** T-M5-04a: 隐私同意快速检查。 */
    val consentStore: ConsentStore by lazy {
        ConsentStore(this)
    }

    /** T-M5-05b: 崩溃报告本地库（独立于业务库）。 */
    val crashDb: CrashReportDatabase by lazy {
        CrashReportDatabase.getInstance(this)
    }

    /** T-M5-05b: 全局崩溃捕获 + 下次启动上报。 */
    val crashReporter: CrashReporter by lazy {
        CrashReporter(
            context = this,
            crashDb = crashDb,
            consentStore = consentStore,
        )
    }

    override fun onCreate() {
        super.onCreate()
        // T-M5-05b: 立即安装全局 UncaughtExceptionHandler —— 越早越好，
        // 任何依赖（auth、sync）尚未初始化时也可能崩溃。
        crashReporter.install()
        // 上传上一次运行遗留的崩溃报告（仅在用户已同意隐私政策后）。
        crashReporter.uploadPending(
            authClient = authClient,
            httpClient = httpClient,
            baseUrl = BuildConfig.SYNC_BASE_URL,
        )
    }

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().build()
}
