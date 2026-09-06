package com.aozora.app

import android.annotation.SuppressLint
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
import com.aozora.app.sync.SyncScheduler
import com.aozora.app.sync.SyncState
import com.aozora.app.sync.SyncStatusEvent
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var app: AozoraApp
    private lateinit var database: AozoraDatabase
    private lateinit var repository: Repository
    private lateinit var bridge: AozoraBridge
    private val scope = MainScope()

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        app = application as AozoraApp
        database = app.database
        repository = app.repository

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
        }

        bridge = AozoraBridge(
            repository = repository,
            webView = webView,
            scope = scope,
            syncEngine = app.syncEngine,
            syncContext = this
        )
        webView.addJavascriptInterface(bridge, "AozoraBridge")

        setContentView(webView)
        webView.loadUrl("https://appassets.androidplatform.net/assets/index.html")

        // T-M3-02: 启动时收集同步状态事件 → 桥接到前端；同时触发一轮同步。
        observeSyncStatus()
        SyncScheduler.enqueue(this)
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
        // P2-9: 补全 \r \t     转义
        s.replace("\\", "\\\\")
         .replace("\"", "\\\"")
         .replace("\n", "\\n")
         .replace("\r", "\\r")
         .replace("\t", "\\t")
         .replace(" ", "\\u2028")
         .replace(" ", "\\u2029")
}
