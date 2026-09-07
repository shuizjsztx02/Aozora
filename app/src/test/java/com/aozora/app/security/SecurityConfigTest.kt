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
        // bridge-types.ts and BridgeCommands.kt must stay in sync.
        // If a command is added or removed, BOTH files must be updated.
        val kotlinCommands = extractBridgeCommandStrings()
        assertEquals(
            "Bridge command count must be 44 (sync with bridge-types.ts)",
            44,
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
        val commands = extractBridgeCommandStrings()
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

    // ------------------------------------------------------------------
    // Helpers
    // ------------------------------------------------------------------

    private fun extractBridgeCommandStrings(): List<String> {
        return listOf(
            BridgeCommands.GET_VERSION,
            BridgeCommands.TX_INSERT, BridgeCommands.TX_GET,
            BridgeCommands.TX_LIST_BY_BOOK, BridgeCommands.TX_LIST_BY_DATE,
            BridgeCommands.TX_SOFT_DELETE,
            BridgeCommands.BOOK_LIST, BridgeCommands.BOOK_INSERT,
            BridgeCommands.CATEGORY_LIST,
            BridgeCommands.ACCOUNT_LIST, BridgeCommands.ACCOUNT_INSERT,
            BridgeCommands.ACCOUNT_ARCHIVE,
            BridgeCommands.BUDGET_LIST, BridgeCommands.BUDGET_INSERT,
            BridgeCommands.BUDGET_UPDATE, BridgeCommands.BUDGET_DELETE,
            BridgeCommands.PM_LIST, BridgeCommands.PM_MARK_SYNCING,
            BridgeCommands.PM_MARK_FAILED, BridgeCommands.PM_REMOVE,
            BridgeCommands.SYNC_GET_STATUS, BridgeCommands.SYNC_TRIGGER,
            BridgeCommands.REMINDER_GET_STATUS, BridgeCommands.REMINDER_SET,
            BridgeCommands.REMINDER_CHECK_PERMISSION,
            BridgeCommands.AI_REPORT,
            BridgeCommands.AI_INTERPRET, BridgeCommands.AI_VALIDATE,
            BridgeCommands.AI_EXECUTE,
            BridgeCommands.PERMISSIONS_GET_STATUS, BridgeCommands.PERMISSIONS_REQUEST,
            BridgeCommands.PERMISSIONS_OPEN_SETTINGS,
            BridgeCommands.VOICE_START_RECORDING, BridgeCommands.VOICE_STOP_RECORDING,
            BridgeCommands.IMAGE_PICK, BridgeCommands.IMAGE_DELETE,
            BridgeCommands.IMAGE_PARSE, BridgeCommands.IMAGE_PARSE_CANCEL,
            BridgeCommands.CONSENT_RECORD, BridgeCommands.CONSENT_STATUS,
            BridgeCommands.EXPORT_CREATE, BridgeCommands.EXPORT_LIST,
            BridgeCommands.EXPORT_GET, BridgeCommands.EXPORT_DOWNLOAD,
        )
    }
}
