package com.aozora.app.bridge

import android.content.Context
import android.webkit.WebView
import androidx.activity.ComponentActivity
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.image.ImagePicker
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.Robolectric
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T16-5（REQ-61 / 决策 #7）：theme.pickBackground 桥命令测试。
 *
 * 直走 AozoraBridge.dispatch（internal，R-M4-21 先例）验证：
 * - 命令已注册到 dispatch（未注入依赖时返回 unavailable，而非 unknown_command）
 * - 压缩参数按全屏背景独立定，非 icon.pick 的 128px/48KB 链路
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraBridgeThemeBackgroundTest {

    private lateinit var context: Context
    private lateinit var db: AozoraDatabase

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        db = AozoraDatabase.createInMemory(context)
    }

    @After
    fun tearDown() {
        db.close()
    }

    private fun newBridge(
        syncContext: Context? = context,
        imagePicker: ImagePicker? = null,
    ) = AozoraBridge(
        Repository(db),
        WebView(context),
        syncContext = syncContext,
        imagePicker = imagePicker,
    )

    /** Robolectric 下构造真实 ImagePicker（仅用于通过依赖检查，不触发选图）。 */
    private fun realPicker(): ImagePicker {
        val activity = Robolectric.buildActivity(ComponentActivity::class.java).get()
        return ImagePicker(activity).apply { registerLaunchers() }
    }

    @Test
    fun pickBackground_isDispatched_andWithoutPickerReturnsUnavailable(): Unit = runBlocking {
        val response = newBridge(imagePicker = null)
            .dispatch("req-1", BridgeCommands.THEME_PICK_BACKGROUND, JSONObject())
        assertFalse(response.success)
        assertEquals("unavailable", response.errorCode)
    }

    @Test
    fun pickBackground_withoutContextReturnsUnavailable(): Unit = runBlocking {
        val response = newBridge(syncContext = null, imagePicker = realPicker())
            .dispatch("req-2", BridgeCommands.THEME_PICK_BACKGROUND, JSONObject())
        assertFalse(response.success)
        assertEquals("unavailable", response.errorCode)
    }

    @Test
    fun pickBackground_unknownSourceReturnsInvalidInput(): Unit = runBlocking {
        val response = newBridge(imagePicker = realPicker())
            .dispatch("req-3", BridgeCommands.THEME_PICK_BACKGROUND, JSONObject().put("source", "screen"))
        assertFalse(response.success)
        assertEquals("invalid_input", response.errorCode)
    }

    @Test
    fun backgroundCompressionParams_areFullscreenTuned() {
        assertEquals("全屏背景不得降级为图标缩略图尺寸", 1080, AozoraBridge.BG_MAX_DIMENSION)
        assertEquals(70, AozoraBridge.BG_QUALITY)
        assertTrue("成品上限必须显著高于 icon.pick 的 48KB", AozoraBridge.BG_MAX_BYTES > 48 * 1024)
    }
}