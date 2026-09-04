package com.aozora.app.bridge

import android.view.ViewGroup
import android.webkit.WebView
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.aozora.app.MainActivity
import org.json.JSONObject
import org.json.JSONTokener
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

/**
 * 真正执行打包后的模块脚本和 Kotlin → JavaScript 回调；必须在设备上运行。
 *
 * R-M1-45 注意：WebView 测试在某些设备上可能不稳定（USB 安装弹窗超时等）。
 * 建议单独运行而非在 CI 中全量跑：
 *   adb shell am instrument -w -e class com.aozora.app.bridge.WebViewBridgeTest ...
 */
@RunWith(AndroidJUnit4::class)
class WebViewBridgeTest {
    private fun evaluate(scenario: ActivityScenario<MainActivity>, script: String): String {
        val done = CountDownLatch(1)
        val value = AtomicReference<String>()
        scenario.onActivity { activity ->
            val webView = activity.findViewById<ViewGroup>(android.R.id.content).getChildAt(0) as WebView
            webView.evaluateJavascript(script) { value.set(it); done.countDown() }
        }
        assertTrue("JS callback timed out", done.await(5, TimeUnit.SECONDS))
        return value.get()
    }

    private fun waitFor(scenario: ActivityScenario<MainActivity>, condition: String) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
        while (System.nanoTime() < deadline) {
            if (evaluate(scenario, condition) == "true") return
            Thread.sleep(100)
        }
        fail("WebView condition timed out: $condition")
    }

    @Test
    fun bundledReactAndNativeCallbackWorkOverHttps() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            waitFor(scenario, "document.querySelector('#root main') !== null")
            assertEquals("\"https://appassets.androidplatform.net\"", evaluate(scenario, "location.origin"))
            evaluate(scenario, "window.__aozoraBridgeRespond = value => { window.__testReply = value; }")
            evaluate(scenario, "AozoraBridge.call(1, 'probe', 'bridge.getVersion', '{}')")
            waitFor(scenario, "typeof window.__testReply === 'string'")
            val reply = JSONObject(JSONTokener(evaluate(scenario, "window.__testReply")).nextValue() as String)
            assertTrue(reply.getBoolean("success"))
            assertEquals(1, reply.getJSONObject("data").getInt("version"))
        }
    }
}
