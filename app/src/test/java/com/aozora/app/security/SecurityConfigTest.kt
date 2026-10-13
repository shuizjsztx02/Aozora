package com.aozora.app.security

import com.aozora.app.bridge.BridgeCommands
import com.aozora.app.bridge.BridgeProtocol
import org.junit.Assert.*
import org.junit.Test

/**
 * T-M5-02a: Security configuration assertions.
 *
 * JVM unit tests that verify security-critical invariants:
 *   • Bridge version and command count match the contract
 *   • Release NSC overlay exists (HTTPS-only)
 *   • AndroidManifest security attributes are set correctly
 *
 * WebView configuration is verified via Robolectric in instrumented tests.
 */
class SecurityConfigTest {

    // ------------------------------------------------------------------
    // Bridge contract integrity
    // ------------------------------------------------------------------

    @Test
    fun bridgeVersion_isV1() {
        assertEquals("Bridge protocol must be v1", 1, BridgeProtocol.VERSION)
    }

    @Test
    fun bridgeCommandCount_matchesTypescriptContract() {
        // BridgeCommands.ALL is the canonical list — adding a new command
        // requires updating BOTH the Kotlin BridgeCommands object and the
        // TypeScript BridgeCommands in bridge-types.ts.  This test fails
        // whenever the list grows, forcing an explicit decision about
        // whether the TS contract was updated in parallel.
        // T7-1: +ACCOUNT_UPDATE（account.update）→ 52；TS 侧由 T7-2 同步。
        // 第八轮: +CATEGORY_INSERT/UPDATE +ICON_PICK +SAVINGS_LIST/INSERT/UPDATE/ARCHIVE（+7）→ 59。
        // T9-5a（REQ-11）: +META_GET/META_SET（meta.get/set，鉴权档与 book.insert 同为写类）→ 61。
        // T10-3（REQ-19，决策 #4）: +AUTH_LOGIN/LOGOUT/ME/UPDATE_PROFILE（auth.*，4 条）→ 65。
        // T11-2（REQ-24，决策 #5）: +WIDGET_REFRESH（widget.refresh）→ 66。
        // T11-4（REQ-26，决策 #4）: +SCHEDULED_LIST/INSERT/UPDATE/ARCHIVE（scheduled.*，4 条）→ 70。
        // T11-7（REQ-28，决策 #8）: +PERSONA_LIST/INSERT/UPDATE/ARCHIVE（persona.*，4 条）→ 74。
        // T12-7（REQ-36，决策 #1）: +TX_UPDATE（transaction.update）→ 75。
        // T14-2（REQ-39，决策 #1）: +BOOK_UPDATE（book.update）→ 76。
        // T16-5（REQ-61，决策 #1/#7）: +THEME_PICK_BACKGROUND（theme.pickBackground）→ 77。
        // T-M7-02h（决策 #1）: +AI_AGENT/AI_CANCEL（ai.agent / ai.cancel）→ 79。
        // T-M7-03d（决策 #1）: +CONVERSATION_LIST/RENAME/DELETE（conversation.*，3 条）→ 82。
        // T-M7-04b（决策 #1）: +CONVERSATION_EVENTS（conversation.events）→ 83。
        //   03d 曾判「不加密钥无人调用」；04b 的服务端改成逐事件落库后，客户端要按
        //   游标续读自己落下的部分，于是有了调用方。
        // T-M7-05f（决策 #1）: +DEVICE_EXECUTE/DEVICE_RESULT（device.execute / device.result）→ 85。
        //   设备能力通道的客户端执行器：前者是「能力名 → 桥命令」的分派 + status
        //   归一化，后者是需结果类的上行回传（Token 不出 Kotlin 层）。
        // T19-7（REQ-75 / 决策 #1）: -VOICE_START_RECORDING/VOICE_STOP_RECORDING → 83。
        //   离线语音（Vosk）整体移除：连同 RECORD_AUDIO 权限族与设备能力
        //   voice.transcribe 一并下架（服务端侧见 T19-8）。
        val kotlinCommands = BridgeCommands.ALL
        assertEquals(
            "Bridge command count must be 83 (sync with bridge-types.ts)",
            83,
            kotlinCommands.size
        )
        assertEquals(
            "Bridge commands must have no duplicates",
            kotlinCommands.size,
            kotlinCommands.toSet().size
        )
    }

    @Test
    fun bridgeCommands_noEmptyOrBlank() {
        val commands = BridgeCommands.ALL
        commands.forEach { cmd ->
            assertTrue("Bridge command must not be blank: '$cmd'", cmd.isNotBlank())
        }
    }

    // ------------------------------------------------------------------
    // NSC overlay files exist
    // ------------------------------------------------------------------

    @Test
    fun releaseNscOverlay_exists() {
        val releaseNsc = java.io.File(
            "src/release/res/xml/network_security_config.xml"
        )
        assertTrue(
            "Release NSC overlay must exist at src/release/res/xml/network_security_config.xml",
            releaseNsc.exists()
        )
    }

    @Test
    fun releaseNsc_noCleartextAllowed() {
        val releaseNsc = java.io.File(
            "src/release/res/xml/network_security_config.xml"
        )
        if (!releaseNsc.exists()) return // skip if missing (other test catches it)
        val content = releaseNsc.readText()
        assertFalse(
            "Release NSC must not have cleartextTrafficPermitted=\"true\"",
            content.contains("cleartextTrafficPermitted=\"true\"")
        )
    }

    @Test
    fun debugNscOverlay_exists() {
        val debugNsc = java.io.File(
            "src/debug/res/xml/network_security_config.xml"
        )
        assertTrue(
            "Debug NSC overlay must exist at src/debug/res/xml/network_security_config.xml",
            debugNsc.exists()
        )
    }

    // ------------------------------------------------------------------
    // Manifest security attributes
    // ------------------------------------------------------------------

    @Test
    fun manifest_allowBackupIsFalse() {
        val manifest = java.io.File("src/main/AndroidManifest.xml")
        if (!manifest.exists()) return
        val content = manifest.readText()
        assertTrue(
            "AndroidManifest must set android:allowBackup=\"false\"",
            content.contains("android:allowBackup=\"false\"")
        )
    }

    @Test
    fun manifest_cleartextTrafficIsFalse() {
        val manifest = java.io.File("src/main/AndroidManifest.xml")
        if (!manifest.exists()) return
        val content = manifest.readText()
        assertTrue(
            "AndroidManifest must set android:usesCleartextTraffic=\"false\"",
            content.contains("android:usesCleartextTraffic=\"false\"")
        )
    }

    @Test
    fun manifest_hasNetworkSecurityConfig() {
        val manifest = java.io.File("src/main/AndroidManifest.xml")
        if (!manifest.exists()) return
        val content = manifest.readText()
        assertTrue(
            "AndroidManifest must reference network_security_config",
            content.contains("android:networkSecurityConfig")
        )
    }

    @Test
    fun manifest_hasDataExtractionRules() {
        val manifest = java.io.File("src/main/AndroidManifest.xml")
        if (!manifest.exists()) return
        val content = manifest.readText()
        assertTrue(
            "AndroidManifest must declare android:dataExtractionRules (API 31+)",
            content.contains("android:dataExtractionRules")
        )
    }

    // ------------------------------------------------------------------
    // 第二十一轮 REQ-83（T21-3）：自动记账的两条红线
    //
    // 采集走「无障碍服务 + 通知监听」双通道，两条通道都靠 <service> +
    // android:permission 声明、授权在系统设置页 —— 因此 <uses-permission> 集合
    // **逐条不变**（决策 #3）。这不是"顺便看看"：权限面是这个能力对外最硬的
    // 承诺（不读短信、不用悬浮窗），而且一旦被静默放宽，仓库里没有任何别的
    // 机制会发现。故此处按"逐条钉住"而不是"不许出现某几个"来守
    // （`AI协作稳定性守则.md` §3.1：跨层契约每一处都要有守护）。
    // ------------------------------------------------------------------

    /** 期望的 <uses-permission> 全集。**新增权限必须显式改这里**，改不动就别加。 */
    private val expectedUsesPermissions = setOf(
        "android.permission.INTERNET",
        "android.permission.VIBRATE",
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.RECEIVE_BOOT_COMPLETED",
        "android.permission.SCHEDULE_EXACT_ALARM",
        "com.aozora.app.permission.BRIDGE_COMMAND",
        "android.permission.CAMERA",
        "android.permission.READ_MEDIA_IMAGES",
        "android.permission.READ_EXTERNAL_STORAGE",
    )

    /** 明确禁止的高危权限（各自对应一条产品承诺，见 ADR-016 决策 2）。 */
    private val forbiddenPermissions = listOf(
        "android.permission.READ_SMS",
        "android.permission.RECEIVE_SMS",
        "android.permission.SMS_RECEIVED",
        "android.permission.SYSTEM_ALERT_WINDOW",
        "android.permission.RECORD_AUDIO",
    )

    private fun usesPermissions(content: String): Set<String> =
        Regex("<uses-permission\\s+android:name=\"([^\"]+)\"").findAll(content)
            .map { it.groupValues[1] }
            .toSet()

    @Test
    fun manifest_usesPermissionSetIsUnchangedByAutobook() {
        val manifest = java.io.File("src/main/AndroidManifest.xml")
        if (!manifest.exists()) return
        val actual = usesPermissions(manifest.readText())
        assertEquals(
            "自动记账靠 <service> 授权，不得新增 <uses-permission>（ADR-016 决策 2）",
            expectedUsesPermissions,
            actual
        )
    }

    @Test
    fun manifest_hasNoSmsOrOverlayPermissions() {
        val manifest = java.io.File("src/main/AndroidManifest.xml")
        if (!manifest.exists()) return
        val content = manifest.readText()
        forbiddenPermissions.forEach { perm ->
            assertFalse(
                "AndroidManifest must not request $perm (ADR-016 决策 2)",
                content.contains(perm)
            )
        }
    }

    @Test
    fun manifest_declaresNotificationListenerServiceWithBindPermission() {
        val manifest = java.io.File("src/main/AndroidManifest.xml")
        if (!manifest.exists()) return
        val content = manifest.readText()
        assertTrue(
            "通知监听服务必须以 BIND_NOTIFICATION_LISTENER_SERVICE 声明",
            content.contains("android.permission.BIND_NOTIFICATION_LISTENER_SERVICE")
        )
        assertTrue(
            "通知监听服务必须声明 .autobook.AutoBookNotificationListener",
            content.contains(".autobook.AutoBookNotificationListener")
        )
        assertTrue(
            "通知监听服务必须带 NotificationListenerService 的 intent-filter action",
            content.contains("android.service.notification.NotificationListenerService")
        )
    }
}
