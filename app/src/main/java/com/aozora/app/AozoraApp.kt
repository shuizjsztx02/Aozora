package com.aozora.app

import android.app.Application
import androidx.work.Configuration
import com.aozora.app.ai.AiGatewayClient
import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
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

    /** T-M4-04b: Vosk 离线语音识别服务。
     *  首次使用时需调用 initialize() 加载模型（约 1-2 秒）。 */
    val voiceRecognitionService: VoiceRecognitionService by lazy {
        VoiceRecognitionService(this)
    }

    override val workManagerConfiguration: Configuration
        get() = Configuration.Builder().build()
}
