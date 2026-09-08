package com.aozora.app.consent

import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * T-M5-04a review P3#4: ConsentStore unit tests.
 *
 * ConsentStore is the fast-path gate checked by SyncEngine / onboarding
 * before allowing sync; its two boolean flags (consented / uploaded) must
 * survive mark/clear cycles correctly.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])  // Robolectric 4.11.1 caps at SDK 34; project tests pin 33
class ConsentStoreTest {

    private lateinit var store: ConsentStore

    @Before
    fun setUp() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        store = ConsentStore(context)
    }

    @Test
    fun `hasConsented defaults to false on fresh store`() {
        assertFalse(store.hasConsented())
    }

    @Test
    fun `markConsented flips hasConsented to true`() {
        store.markConsented()
        assertTrue(store.hasConsented())
    }

    @Test
    fun `clear resets consented back to false`() {
        store.markConsented()
        store.clear()
        assertFalse(store.hasConsented())
    }

    @Test
    fun `hasUploadedConsent defaults to false`() {
        assertFalse(store.hasUploadedConsent())
    }

    @Test
    fun `markConsentUploaded flips uploaded flag without touching consented`() {
        store.markConsented()
        store.markConsentUploaded()
        assertTrue(store.hasUploadedConsent())
        assertTrue(store.hasConsented())
    }

    @Test
    fun `clear also resets uploaded flag`() {
        // Account deletion (wipeLocalState) must fully reset consent state:
        // a stale uploaded=true would suppress the cold-start re-upload retry
        // (AozoraApp.onCreate) for a fresh consent after re-registration.
        store.markConsented()
        store.markConsentUploaded()
        store.clear()
        assertFalse(store.hasConsented())
        assertFalse(store.hasUploadedConsent())
    }

    @Test
    fun `new store instance for same context observes persisted flags`() {
        store.markConsented()
        store.markConsentUploaded()
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val reopened = ConsentStore(context)
        assertTrue(reopened.hasConsented())
        assertTrue(reopened.hasUploadedConsent())
    }
}
