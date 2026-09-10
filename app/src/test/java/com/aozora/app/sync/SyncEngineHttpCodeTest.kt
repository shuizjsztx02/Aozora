package com.aozora.app.sync

import android.content.Context
import com.aozora.app.auth.AuthClient
import com.aozora.app.auth.TokenStore
import com.aozora.app.db.AozoraDatabase
import com.aozora.app.db.Repository
import com.aozora.app.db.entity.BookEntity
import com.aozora.app.db.entity.CategoryEntity
import com.aozora.app.db.entity.TransactionEntity
import kotlinx.coroutines.runBlocking
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config

/**
 * T12 后修复（图片记账 403 身份漂移案）：SyncEngine 对 SyncHttpException 的
 * 状态码判定测试。
 *
 * - 403（consent_required / account_frozen 业务态）→ ERROR + RETRYABLE，
 *   **不得** clearAuth（旧口径会抹掉合法会话，guest 重注册换身份后
 *   /ai/execute 因服务端新账号无任何分类而报 rejected_hallucination 403）。
 * - 401（凭证被拒）→ AUTH_LOST + clearAuth（保留旧行为）。
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [33])
class SyncEngineHttpCodeTest {

    /** push/pull 行为可配置的 SyncApi 替身。 */
    private class FakeApi : SyncApi {
        var pullThrows: SyncHttpException? = null
        var pushThrows: SyncHttpException? = null
        var pullCalls = 0
        var pushCalls = 0
        var nextCursor = 5L

        override suspend fun push(request: PushRequest): PushResponse {
            pushCalls++
            pushThrows?.let { throw it }
            return PushResponse(
                acceptedMutationIds = request.mutations.map { it.mutationId },
                rejected = emptyList(),
                nextCursor = nextCursor
            )
        }

        override suspend fun pull(request: PullRequest): PullResponse {
            pullCalls++
            pullThrows?.let { throw it }
            return PullResponse(emptyList(), nextCursor)
        }

        override suspend fun bootstrap(): BootstrapResponse =
            throw IllegalStateException("bootstrap not expected (books seeded)")
    }

    /** 记录 clearAuth 次数的 AuthClient 替身。 */
    private class StubAuth(context: Context) : AuthClient(
        baseUrl = "http://fake",
        tokenStore = TokenStore(context),
        httpClient = OkHttpClient()
    ) {
        var clearAuthCalls = 0
        override suspend fun ensureValidAccessToken(): String = "fake-token"
        override fun currentUserId(): String? = "u1"
        override fun clearAuth() { clearAuthCalls++ }
    }

    private lateinit var db: AozoraDatabase
    private lateinit var repo: Repository
    private lateinit var api: FakeApi
    private lateinit var auth: StubAuth
    private lateinit var engine: SyncEngine

    @Before
    fun setUp() {
        val ctx = RuntimeEnvironment.getApplication()
        db = AozoraDatabase.createInMemory(ctx)
        repo = Repository(db)
        api = FakeApi()
        auth = StubAuth(ctx)
        engine = SyncEngine(repo, api, auth)
    }

    @After
    fun tearDown() { db.close() }

    /** 播种 book+category+transaction：本地实体非空 → needsBootstrap=false；
     *  transaction 附带 pending mutation → push 阶段会真正调用 syncApi.push。 */
    private suspend fun seedBookCategoryAndTx() {
        repo.insertBook(
            BookEntity(id = "b1", ownerId = "u1", name = "book", icon = "default",
                version = 1, archivedAt = null),
            mutationId = "m-book"
        )
        repo.insertCategory(
            CategoryEntity(id = "c1", ownerId = "u1", bookId = null, parentId = null,
                kind = "expense", name = "餐饮", icon = null, archivedAt = null, version = 1),
            mutationId = "m-cat"
        )
        repo.insertTransaction(
            TransactionEntity(id = "t1", bookId = "b1", categoryId = "c1", accountId = null,
                amountCents = 100L, type = "expense", bizDate = "2026-09-10",
                occurredAt = 1000L, note = "", source = "manual",
                version = 1, deletedAt = null),
            mutationId = "m-tx"
        )
    }

    @Test
    fun pushForbidden403_isRetryable_andKeepsAuth() = runBlocking {
        seedBookCategoryAndTx()
        api.pushThrows = SyncHttpException(403, "consent_required")

        val outcome = engine.runOnce()

        assertEquals(SyncEngine.SyncOutcome.RETRYABLE, outcome)
        assertEquals("403 must not clear credentials", 0, auth.clearAuthCalls)
        assertEquals(SyncState.ERROR, engine.status.value.state)
        assertTrue("push should have been attempted", api.pushCalls >= 1)
    }

    @Test
    fun pullForbidden403_isRetryable_andKeepsAuth() = runBlocking {
        seedBookCategoryAndTx()
        api.pullThrows = SyncHttpException(403, "account_frozen")

        val outcome = engine.runOnce()

        assertEquals(SyncEngine.SyncOutcome.RETRYABLE, outcome)
        assertEquals(0, auth.clearAuthCalls)
    }

    @Test
    fun pullUnauthorized401_isAuthLost_andClearsAuth() = runBlocking {
        seedBookCategoryAndTx()
        api.pullThrows = SyncHttpException(401, "token rejected")

        val outcome = engine.runOnce()

        assertEquals(SyncEngine.SyncOutcome.AUTH_LOST, outcome)
        assertEquals(1, auth.clearAuthCalls)
        assertEquals(SyncState.AUTH_LOST, engine.status.value.state)
        assertFalse("401 时不应推进 push 阶段", api.pushCalls > 0)
    }
}
