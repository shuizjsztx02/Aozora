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
        val kotlinCommands = BridgeCommands.ALL
        assertEquals(
            "Bridge command count must be 76 (sync with bridge-types.ts)",
            76,
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
}
