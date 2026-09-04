package com.aozora.app.bridge

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class BridgePayloadTest {
    @Test
    fun rejectsFractionStringAndUnsafeInteger() {
        for (value in listOf(1.5, "100", 9007199254740992L, JSONObject.NULL)) {
            try {
                BridgePayload.safeLong(JSONObject().put("amountCents", value), "amountCents")
                fail("invalid value accepted: $value")
            } catch (_: IllegalArgumentException) { }
        }
        assertEquals(100L, BridgePayload.safeLong(JSONObject().put("amountCents", 100), "amountCents"))
    }

    @Test
    fun nullableAccountKeepsNullRatherThanStringNull() {
        assertNull(BridgePayload.optionalString(JSONObject("""{"accountId":null}"""), "accountId"))
        assertEquals("cash", BridgePayload.optionalString(JSONObject("""{"accountId":"cash"}"""), "accountId"))
    }
}
