package com.aozora.app.bridge

import android.content.Context
import android.webkit.WebView
import androidx.appcompat.app.AppCompatActivity
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.permission.PermissionManager
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
import org.robolectric.Shadows.shadowOf
import org.robolectric.annotation.Config

/**
 * T-M7-05f：设备能力通道的**客户端执行器**（桥侧）测试。
 *
 * 经 `AozoraBridge.dispatch`（internal，R-M4-21 先例）直走真实 handler 路径，断言：
 * - `device.execute` 的**分派**：能力名 → 既有设备类 handler；
 * - `device.execute` 的**归一化**：各 handler 的错误码族 → 协议四值
 *   （ok / denied / unavailable / failed）——这是 WebView 与模型都不必认识桥错误码的
 *   那个收口点；
 * - `device.result` 的**入参校验**与桥缺席时的降级（真回传走 HTTP，由
 *   `AiGatewayClient` 的用例与端到端走查覆盖：这里没有可用的 AI 客户端）。
 *
 * 语音转写（`voice.transcribe`）**不在本层**：它由 WebView 侧的 `device-executor`
 * 编排（`deferred`），用例见 `app/src/__tests__/features/device-executor.test.ts`。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class AozoraBridgeDeviceTest {

    private lateinit var context: Context
    private lateinit var db: AozoraDatabase
    private lateinit var repository: Repository
    private lateinit var activity: AppCompatActivity
    private lateinit var permissionManager: PermissionManager

    @Before
    fun setUp() {
        context = RuntimeEnvironment.getApplication()
        db = AozoraDatabase.createInMemory(context)
        repository = Repository(db)
        val controller = Robolectric.buildActivity(AppCompatActivity::class.java).create()
        activity = controller.get()
        permissionManager = PermissionManager(activity)
        // 刻意**不**注册 launcher：未注册时 requestPermission 立即回调 DENIED（已有语义），
        // 这正是「用户当场拒绝」那条路径的确定性替身。
        controller.resume()
    }

    @After
    fun tearDown() {
        db.close()
    }

    private fun newBridge(
        syncContext: Context? = context,
        manager: PermissionManager? = permissionManager,
    ) = AozoraBridge(
        repository,
        WebView(context),
        syncContext = syncContext,
        permissionManager = manager,
    )

    private fun execute(
        bridge: AozoraBridge, capability: String, payload: JSONObject = JSONObject(),
    ): JSONObject = runBlocking {
        val resp = bridge.dispatch(
            "req-1", BridgeCommands.DEVICE_EXECUTE,
            JSONObject().put("capability", capability).put("payload", payload),
        )
        assertTrue("dispatch 必须成功应答：${resp.errorMessage}", resp.success)
        JSONObject(resp.data!!)
    }

    // ─── device.execute：分派 ───

    @Test
    fun deviceExecute_unknownCapability_isUnavailable(): Unit = runBlocking {
        // 未知能力按「设备上不可用」结算（服务端 ERR_DEVICE_UNAVAILABLE 的文案就是
        // 「权限被永久拒绝或设备不支持」），不是 failed——两者的模型侧含义不同。
        val data = execute(newBridge(), "nope.unknown")
        assertEquals("unavailable", data.getString("status"))
        assertFalse(data.has("data"))
    }

    @Test
    fun deviceExecute_missingCapability_isInvalidInput(): Unit = runBlocking {
        val resp = newBridge().dispatch("req-1", BridgeCommands.DEVICE_EXECUTE, JSONObject())
        assertFalse(resp.success)
        assertEquals("invalid_input", resp.errorCode)
    }

    @Test
    fun deviceExecute_widgetRefresh_withoutContext_isUnavailable(): Unit = runBlocking {
        val data = execute(newBridge(syncContext = null), "widget.refresh")
        assertEquals("unavailable", data.getString("status"))
    }

    @Test
    fun deviceExecute_widgetRefresh_withContext_isOk(): Unit = runBlocking {
        val data = execute(newBridge(), "widget.refresh")
        assertEquals("ok", data.getString("status"))
    }

    @Test
    fun deviceExecute_haptic_isOk(): Unit = runBlocking {
        // 触感是单向能力：没有振动器时既有 handler 也回成功（设备不支持震动不是失败）
        val data = execute(newBridge(), "haptic.feedback", JSONObject().put("kind", "success"))
        assertEquals("ok", data.getString("status"))
    }

    @Test
    fun deviceExecute_shareMissingText_isFailed(): Unit = runBlocking {
        // 既有 handler 的 invalid_input（模型给了空文本）→ 归一化成 failed：
        // 与「用户拒绝」（denied）不同，模型可以换一段文本重试。
        val data = execute(newBridge(), "share.text")
        assertEquals("failed", data.getString("status"))
    }

    @Test
    fun deviceExecute_imagePick_withoutPicker_isUnavailable(): Unit = runBlocking {
        // 未注入 picker（如测试构造 / 未配置环境）——既有 handler 回 unavailable
        val data = execute(newBridge(), "image.pick", JSONObject().put("source", "gallery"))
        assertEquals("unavailable", data.getString("status"))
    }

    // ─── device.execute：permissions.request 的四态 ───

    @Test
    fun deviceExecute_permission_granted_isOkWithStatus(): Unit = runBlocking {
        shadowOf(activity).grantPermissions(android.Manifest.permission.CAMERA)
        val data = execute(newBridge(), "permissions.request", JSONObject().put("permission", "CAMERA"))
        assertEquals("ok", data.getString("status"))
        // ok 的 data 会回喂模型——三态里能到达模型的那一个必须自带说明
        assertEquals("CAMERA", data.getJSONObject("data").getString("permission"))
        assertEquals("granted", data.getJSONObject("data").getString("status"))
    }

    @Test
    fun deviceExecute_permission_denied_isDenied(): Unit = runBlocking {
        val data = execute(
            newBridge(), "permissions.request", JSONObject().put("permission", "CAMERA"),
        )
        assertEquals("denied", data.getString("status"))
        assertFalse(data.has("data"))
    }

    @Test
    fun deviceExecute_permission_permanentlyDenied_isUnavailable(): Unit = runBlocking {
        // 永久拒绝 = 「去设置页」而不是「别再问」——服务端 ERR_DEVICE_UNAVAILABLE 的
        // 文案原文就是「权限被永久拒绝或设备不支持」，两者不能混成一个 denied。
        shadowOf(activity).denyPermissions(android.Manifest.permission.CAMERA)
        activity.getSharedPreferences("aozora_permissions", Context.MODE_PRIVATE)
            .edit()
            .putBoolean("perm_requested_${android.Manifest.permission.CAMERA}", true)
            .apply()
        val data = execute(
            newBridge(), "permissions.request", JSONObject().put("permission", "CAMERA"),
        )
        assertEquals("unavailable", data.getString("status"))
    }

    @Test
    fun deviceExecute_permission_unknownName_isFailed(): Unit = runBlocking {
        val data = execute(
            newBridge(), "permissions.request", JSONObject().put("permission", "NO_SUCH_PERMISSION"),
        )
        assertEquals("failed", data.getString("status"))
    }

    @Test
    fun deviceExecute_permission_withoutManager_isUnavailable(): Unit = runBlocking {
        val data = execute(
            newBridge(manager = null), "permissions.request",
            JSONObject().put("permission", "CAMERA"),
        )
        assertEquals("unavailable", data.getString("status"))
    }

    // ─── device.result：入参校验与桥缺席 ───

    @Test
    fun deviceResult_missingRequestId_isInvalidInput(): Unit = runBlocking {
        val resp = newBridge().dispatch(
            "req-1", BridgeCommands.DEVICE_RESULT, JSONObject().put("status", "ok"),
        )
        assertFalse(resp.success)
        assertEquals("invalid_input", resp.errorCode)
    }

    @Test
    fun deviceResult_timeoutStatus_isRejected(): Unit = runBlocking {
        // `timeout` 只能由服务端产生（挂起期满）——客户端报它没有意义，服务端在
        // 协议边界上也会拒（DeviceChannel.deliver 的第二道）。这里挡在更前面。
        val resp = newBridge().dispatch(
            "req-1", BridgeCommands.DEVICE_RESULT,
            JSONObject().put("request_id", "dev-1").put("status", "timeout"),
        )
        assertFalse(resp.success)
        assertEquals("invalid_input", resp.errorCode)
    }

    @Test
    fun deviceResult_withoutAiClient_isUnavailable(): Unit = runBlocking {
        val resp = newBridge().dispatch(
            "req-1", BridgeCommands.DEVICE_RESULT,
            JSONObject().put("request_id", "dev-1").put("status", "ok"),
        )
        assertFalse(resp.success)
        assertEquals("unavailable", resp.errorCode)
    }
}