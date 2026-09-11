package com.aozora.app

import android.app.Application
import androidx.work.Configuration
import com.aozora.app.ai.AiGatewayClient
import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import com.aozora.app.consent.ConsentClient
import com.aozora.app.consent.ConsentStore
import com.aozora.app.crashlytics.CrashReportDatabase
import com.aozora.app.crashlytics.CrashReporter
import com.aozora.app.crashlytics.HeartbeatClient
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.export.ExportClient
import com.aozora.app.feedback.FeedbackClient
import com.aozora.app.image.ImageClient
import com.aozora.app.reminder.ReminderScheduler
import com.aozora.app.scheduled.ScheduledTxEngine
import com.aozora.app.scheduled.ScheduledTxReceiver
import com.aozora.app.sync.HttpSyncApi
import com.aozora.app.sync.SyncEngine
import com.aozora.app.voice.VoiceRecognitionService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
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

    // T-M5-05ab fix (N-4): heartbeat scope retained as a class member so the
    // launch is not a fire-and-forget coroutine. Matches the S2-7 fix applied
    // to CrashReporter.uploadScope.
    private val heartbeatScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

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
            authClient = authClient,
            userAgent = "Aozora-Android/${BuildConfig.VERSION_NAME ?: "unknown"}"
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

    /** T-M5-05c fix (S1): 上传隐私同意到服务端（POST /consent）。在 handleConsentRecord 成功后异步调用。 */
    val consentClient: ConsentClient by lazy {
        ConsentClient(
            baseUrl = BuildConfig.SYNC_BASE_URL,
            httpClient = httpClient,
            tokenStore = tokenStore,
        )
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
            // T-M5-05ab fix (S2-2): TokenStore 提供 device_id，CrashReporter 在
            // captureSync 中哈希为 SHA-256 填入崩溃报告，供服务端 per-device 限流。
            tokenStore = tokenStore,
        )
    }

    /** T-M5-05ab fix (S1-3): 启动心跳客户端 —— 每次启动向服务端发一次 active 信号。 */
    private val heartbeatClient: HeartbeatClient by lazy {
        HeartbeatClient(
            baseUrl = BuildConfig.SYNC_BASE_URL,
            httpClient = httpClient,
            authClient = authClient,
            tokenStore = tokenStore,
        )
    }

    /** T-M5-05c: 应用内反馈客户端（与 sync 共用连接池）。 */
    val feedbackClient: FeedbackClient by lazy {
        FeedbackClient(
            context = this,
            baseUrl = BuildConfig.SYNC_BASE_URL,
            httpClient = httpClient,
            authClient = authClient,
            tokenStore = tokenStore,
        )
    }

    override fun onCreate() {
        super.onCreate()
        // 第六轮 Bug-5.1：首启种子默认账本/分类。runBlocking 仅为首启排序保证——
        // 后续 SyncScheduler / 聊天记账入口读取时必须已有默认数据；
        // 实际查询/写入都在 Room 自身线程池，主线程阻塞为毫秒级，且幂等（非空即跳过）。
        kotlinx.coroutines.runBlocking {
            repository.ensureSeedDefaults()
            // T11-7（REQ-28 决策 #8）：内置 5 聊天角色播种（personas_seeded 标记，
            // 删光不重播）；紧挨账本/分类种子，保证聊天页首帧即有角色可加载。
            repository.ensureSeedPersonas()
            // T11-1（REQ-23 决策 #2）：预算沿用冷启动触发点；开关非 '1' 时立即返回，幂等。
            repository.ensureBudgetCarryover()
            // T11-4（REQ-26 决策 #4）/ T14-5（REQ-43）：冷启动补跑「日命中且已过
            // 执行时刻」的规则（漏日不补，已知限制）；与闹钟经 lastRunDate 当日去重。
            ScheduledTxEngine(repository).runDueNow()
            // T14-5：B2 单闹钟链起点——scheduleNext 改 suspend（查规则算最近触发时刻），
            // 挪进 runBlocking 保持"补跑后重排"顺序（重启恢复由 BootReceiver 承担）。
            ScheduledTxReceiver.scheduleNext(applicationContext)
        }
        // T-M5-05b: 立即安装全局 UncaughtExceptionHandler —— 越早越好，
        // 任何依赖（auth、sync）尚未初始化时也可能崩溃。
        crashReporter.install()
        // 上传上一次运行遗留的崩溃报告（仅在用户已同意隐私政策后）。
        crashReporter.uploadPending(
            authClient = authClient,
            httpClient = httpClient,
            baseUrl = BuildConfig.SYNC_BASE_URL,
        )
        // T-M5-05ab fix (S1-3 + N-4): 启动时发心跳，使用类级 heartbeatScope 保留引用。
        heartbeatScope.launch {
            try {
                val version = packageManager.getPackageInfo(packageName, 0).versionName ?: "unknown"
                heartbeatClient.send(version, consentStore.hasConsented())
            } catch (_: Exception) {
                // 心跳失败绝不能影响主流程。
            }
        }
        // T-M5-05c fix (S1): 补发 consent —— 用户首次同意时若网络不可达，
        // 后续冷启动会在此处重试 POST /consent，直到上传成功后标记 consentUploaded。
        if (consentStore.hasConsented() && !consentStore.hasUploadedConsent()) {
            heartbeatScope.launch {
                try {
                    val uploaded = consentClient.send("v1", System.currentTimeMillis())
                    if (uploaded) {
                        consentStore.markConsentUploaded()
                    }
                } catch (_: Exception) {
                    // 重试失败不影响主流程，下次冷启动再试。
                }
            }
        }
    }

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().build()
}
