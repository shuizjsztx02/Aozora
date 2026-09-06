package com.aozora.app

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.WebView
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebViewClient
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.webkit.WebViewAssetLoader
import com.aozora.app.bridge.AozoraBridge
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.permission.PermissionManager
import com.aozora.app.sync.SyncScheduler
import com.aozora.app.sync.SyncState
import com.aozora.app.sync.SyncStatusEvent
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    /** T-M4-05c: 深链路由 — 前端通过此 extra 接收初始/深链路由。 */
    companion object {
        const val EXTRA_ROUTE = "extra_route"
    }

    private lateinit var webView: WebView
    private lateinit var app: AozoraApp
    private lateinit var database: AozoraDatabase
    private lateinit var repository: Repository
    private lateinit var bridge: AozoraBridge
    private lateinit var permissionManager: PermissionManager
    private val scope = MainScope()
    private var webViewReady = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        app = application as AozoraApp
        database = app.database
        repository = app.repository

        // T-M4-04a: 权限状态机（必须在 setContentView 前注册 launcher）
        permissionManager = PermissionManager(this)
        permissionManager.registerLaunchers()

        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = false
        }

        val assetLoader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()
        webView.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                assetLoader.shouldInterceptRequest(request.url)

            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                request.url.scheme != "https" || request.url.host != "appassets.androidplatform.net"

            override fun onPageFinished(view: WebView?, url: String?) {
                super.onPageFinished(view, url)
                webViewReady = true
                // 页面加载完成后，路由深链
                routeDeepLink()
            }
        }

        bridge = AozoraBridge(
            repository = repository,
            webView = webView,
            scope = scope,
            syncEngine = app.syncEngine,
            syncContext = this,
            reminderScheduler = app.reminderScheduler,
            aiGatewayClient = app.aiGatewayClient,
            permissionManager = permissionManager
        )
        webView.addJavascriptInterface(bridge, "AozoraBridge")

        setContentView(webView)
        webView.loadUrl("https://appassets.androidplatform.net/assets/index.html")

        // T-M3-02: 启动时收集同步状态事件 → 桥接到前端；同时触发一轮同步。
        observeSyncStatus()
        SyncScheduler.enqueue(this)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        // WebView 已加载时直接路由
        if (webViewReady) {
            routeDeepLink()
        }
    }

    override fun onResume() {
        super.onResume()
        // 每次回到前台再触发一轮（保证飞机模式恢复后能同步）
        SyncScheduler.enqueue(this)
    }

    private var webViewDestroyed = false

    override fun onDestroy() {
        super.onDestroy()
        webViewDestroyed = true
        scope.cancel()
        webView.removeJavascriptInterface("AozoraBridge")
        webView.destroy()
    }

    /**
     * T-M4-05c: 深链路由。
     *
     * 从 Intent data URI 或 EXTRA_ROUTE 中提取路由目标，
     * 通过 evaluateJavascript 调用前端 `window.__aozoraNavigate__(path)`。
     *
     * 支持的 URI 格式：
     * - aozora://home → /home
     * - aozora://entry → /home（打开快速记账）
     * - aozora://detail → /detail
     * - aozora://reports → /reports
     * - aozora://me → /me
     */
    private fun routeDeepLink() {
        val route = resolveRoute() ?: return
        if (webViewDestroyed) return
        val js = """
            (function(){
              try {
                if (typeof window.__aozoraNavigate__ === 'function') {
                  window.__aozoraNavigate__("${escapeJs(route)}");
                }
              } catch (e) {}
            })();
        """.trimIndent()
        webView.post { webView.evaluateJavascript(js, null) }
    }

    private fun resolveRoute(): String? {
        // 1. 先检查 Intent data URI（通知点击、快捷方式）
        val uri: Uri? = intent?.data
        if (uri != null && uri.scheme == "aozora") {
            return when (uri.host) {
                "home" -> "/home"
                "entry" -> "/home"  // 前端收到后打开 QuickEntrySheet
                "detail" -> "/detail"
                "reports" -> "/reports"
                "me" -> "/me"
                else -> null
            }
        }
        // 2. 检查 EXTRA_ROUTE（快捷方式 extra）
        val extraRoute = intent?.getStringExtra(EXTRA_ROUTE)
        if (!extraRoute.isNullOrBlank()) return extraRoute
        return null
    }

    private fun observeSyncStatus() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                app.syncEngine.status.collect { event ->
                    dispatchSyncEvent(event)
                }
            }
        }
    }

    /**
     * 同步事件 → WebView 桥接。
     *
     * 通过 evaluateJavascript 调用前端全局函数 `window.__aozoraSyncEvent__`。
     * 前端若未注入该函数（旧版本 / 调试场景），错误被静默忽略。
     *
     * N-M3-5 修复：增加 failedMutations 数组序列化，前端可据此展示冲突提示。
     */
    private fun dispatchSyncEvent(event: SyncStatusEvent) {
        // P2-8: 防止 WebView 销毁后仍调用 evaluateJavascript 导致崩溃
        if (webViewDestroyed) return

        val stateJson = when (event.state) {
            SyncState.IDLE -> "idle"
            SyncState.PUSHING -> "pushing"
            SyncState.PULLING -> "pulling"
            SyncState.REBUILDING -> "rebuilding"
            SyncState.AUTH_LOST -> "auth-lost"
            SyncState.ERROR -> "error"
        }
        val lastErrorJs = event.lastError?.let { "\"${escapeJs(it)}\"" } ?: "null"
        val failedMutationsJs = event.failedMutations.joinToString(",") { fm ->
            val reasonJs = fm.reason?.let { "\"${escapeJs(it)}\"" } ?: "null"
            """{"mutationId":"${escapeJs(fm.mutationId)}","entityType":"${escapeJs(fm.entityType)}","entityId":"${escapeJs(fm.entityId)}","operation":"${escapeJs(fm.operation)}","reason":$reasonJs}"""
        }
        val js = """
            (function(){
              try {
                if (typeof window.__aozoraSyncEvent__ === 'function') {
                  window.__aozoraSyncEvent__({
                    state: '$stateJson',
                    pendingCount: ${event.pendingCount},
                    lastSyncedAt: ${event.lastSyncedAt},
                    lastError: $lastErrorJs,
                    failedMutations: [$failedMutationsJs]
                  });
                }
              } catch (e) {}
            })();
        """.trimIndent()
        webView.post { webView.evaluateJavascript(js, null) }
    }

    private fun escapeJs(s: String): String =
        // P2-9: 补全 \r \t                 转义
        s.replace("\\", "\\\\")
         .replace("\"", "\\\"")
         .replace("\n", "\\n")
         .replace("\r", "\\r")
         .replace("\t", "\\t")
         .replace(" ", "\\u2028")
         .replace(" ", "\\u2029")
}
