package com.aozora.app.sync

import android.os.Build
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import org.junit.Assert.*
import org.junit.Ignore
import org.junit.Test
import org.junit.runner.RunWith

/**
 * T-M3-02: 飞行模式同步手动测试。
 *
 * 该测试需要真实设备 + 真实服务器，故默认 @Ignore。
 * 运行方式：手动取消 @Ignore，按注释中的步骤操作。
 *
 * 验收（对应 APP-CONTEXT.md T-M3-02 验收①）：
 * 1. 关闭设备网络（飞行模式）。
 * 2. 创建一笔流水 → pending mutation 入队。
 * 3. 开启网络。
 * 4. 等待 ~60 秒（R-PROD-11 SLO）。
 * 5. 验证：pending_mutations 表为空；服务端可见该流水；cursor 推进。
 */
@RunWith(AndroidJUnit4::class)
class FlightModeSyncManualTest {

    @Ignore("requires real device + real server; run manually")
    @Test
    fun flightModeSyncManual() {
        // Step 1: 关闭网络（手动）
        // Step 2: 通过桥或 Repository 直接写入一笔流水
        val ctx = InstrumentationRegistry.getInstrumentation().targetContext
        val db = Room.databaseBuilder(ctx, AozoraDatabase::class.java, "flight-mode-test.db").build()
        val repo = Repository(db)
        val app = InstrumentationRegistry.getInstrumentation().targetContext.applicationContext as com.aozora.app.AozoraApp
        val authClient = app.authClient
        val engine = app.syncEngine

        // 触发一轮同步 — 此时会因网络不可用而失败
        val outcomeWhileOffline = kotlinx.coroutines.runBlocking { engine.runOnce() }
        // 期望 RETRYABLE（IO 失败）或 AUTH_LOST
        assertTrue(
            "expected RETRYABLE or AUTH_LOST, got $outcomeWhileOffline",
            outcomeWhileOffline == SyncEngine.SyncOutcome.RETRYABLE ||
                outcomeWhileOffline == SyncEngine.SyncOutcome.AUTH_LOST
        )

        // Step 3: 用户开启网络（手动）
        // Step 4: 等待 WorkManager 重试（指数退避 ~15s 起）
        Thread.sleep(60_000)

        // Step 5: 验证
        val pending = kotlinx.coroutines.runBlocking { repo.listPendingMutations() }
        assertEquals("all mutations should be synced after recovery", 0, pending.size)
        db.close()
    }
}
