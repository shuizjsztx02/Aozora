package com.aozora.app.db

import android.content.Context
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.aozora.app.db.entity.TransactionEntity
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Ignore
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 杀进程持久化测试。
 *
 * ⚠️ 重要说明（R-M1-44）：
 * connectedDebugAndroidTest 会把 step1/step2 背靠背执行，中间没有 force-stop，
 * 因此自动化测试只能证明"写读一致"，不能真正验证杀进程持久化。
 *
 * 真正的杀进程验证需要手工执行：
 * 1. 运行 step1_writeDataToRealDatabase
 * 2. 手动执行 `adb shell am force-stop com.aozora.app`
 * 3. 运行 step2_verifyDataAfterKill
 *
 * 本类已标记 @Ignore（R-M1-44），不进入自动化套件，避免假绿。
 */
@RunWith(AndroidJUnit4::class)
@Ignore(
    "R-M1-44: connectedDebugAndroidTest 会把 step1/step2 背靠背执行且中间无 force-stop，" +
        "自动化只能证明写读一致、不能证明杀进程持久化，留着会产生假绿。" +
        "真正的杀进程验证走手工三步协议（写入 → am force-stop → 冷启动校验），结果已记录于 T-M1-review。" +
        "设备测试进 CI 前需以独立 runner/手工门禁替代本类。"
)
class KillProcessPersistenceTest {

    private fun getAppContext(): Context =
        InstrumentationRegistry.getInstrumentation().targetContext

    @Test
    fun step1_writeDataToRealDatabase() {
        runBlocking {
            val context = getAppContext()
            // 使用真实数据库（不是测试数据库）
            val db = AozoraDatabase.getInstance(context)
            val repo = Repository(db)

            // 引用完整性：先创建依赖实体（R-P1-RefInt）
            if (db.bookDao().getById("book-kill") == null) {
                db.bookDao().insert(com.aozora.app.db.entity.BookEntity(
                    "book-kill", "owner-kill", "KillBook", "", 1, null
                ))
            }
            if (db.categoryDao().getById("cat-kill") == null) {
                db.categoryDao().insert(com.aozora.app.db.entity.CategoryEntity(
                    "cat-kill", "owner-kill", null, null, "expense", "KillCat", null, 1
                ))
            }

            val tx = TransactionEntity(
                id = "tx-kill-test",
                bookId = "book-kill",
                type = "expense",
                amountCents = 8888,
                categoryId = "cat-kill",
                accountId = null,
                note = "杀进程测试",
                bizDate = "2026-09-04",
                occurredAt = System.currentTimeMillis(),
                source = "manual",
                version = 1,
                deletedAt = null
            )

            repo.insertTransaction(tx, "mutation-kill-test")

            // 验证数据已写入
            val fetched = repo.getTransaction("tx-kill-test")
            assertNotNull("数据应已写入", fetched)
            assertEquals(8888L, fetched!!.amountCents)

            val mutations = repo.listPendingMutations()
            assertTrue("应有 pending mutation", mutations.isNotEmpty())

            // 注意：不要关闭数据库，让应用保持运行
            // 然后执行 adb shell am force-stop com.aozora.app
            // 再运行 step2_verifyDataAfterKill
        }
    }

    @Test
    fun step2_verifyDataAfterKill() {
        runBlocking {
            val context = getAppContext()
            // 重新获取数据库实例（模拟重启后的行为）
            val db = AozoraDatabase.open(context)
            val repo = Repository(db)

            try {
                // 验证数据在 force-stop 后仍然存在
                val fetched = repo.getTransaction("tx-kill-test")
                assertNotNull("数据在 force-stop 后应存在", fetched)
                assertEquals(8888L, fetched!!.amountCents)
                assertEquals("杀进程测试", fetched.note)

                // 验证 pending mutation 也存在
                val mutation = db.pendingMutationDao().getById("mutation-kill-test")
                assertNotNull("pending mutation 在 force-stop 后应存在", mutation)
                assertEquals("tx-kill-test", mutation!!.entityId)
                assertEquals("insert", mutation.operation)
            } finally {
                db.close()
                // 清理测试数据
                cleanup()
            }
        }
    }

    private fun cleanup() {
        // 直接删除测试数据库文件来清理
        try {
            val context = getAppContext()
            context.deleteDatabase("aozora.db")
        } catch (_: Exception) {
            // 忽略清理错误
        }
    }
}
